"""Automatic checks for AI-generated code (P1.8).

Each check comes from a real failure seen in this project:

A. imports_vs_requirements: the agent's code imports a package that is not in
   the pilot's requirements.txt (failure cases #5, #15).
B. unclosed_resources: a database connection or engine is opened but never
   closed, which breaks on Windows (failure cases #12 and #18 - seen twice).

For documents written by the Documentation Agent (P2.3), two checks look for
hallucinated references - names that sound right but do not exist:

C. docs_paths: a file path in `backticks` that does not exist in the pilot.
D. docs_names: a code name in `backticks` (a function such as `get_engine()`,
   a class such as `ExamSession`, or `Class.method`) that is not defined,
   imported or used anywhere in the pilot's Python code.

The checks only WARN. They are simple rules (heuristics), so they can be
wrong; the human sees the warnings at H3 and makes the decision.
"""

import ast
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Literal

from agents.agent_contracts import ContractModel
from agents.pilot_environment import read_requirements

# Import names that differ from the package name on PyPI.
IMPORT_TO_PACKAGE = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "fitz": "pymupdf",
    "pptx": "python-pptx",
    "docx": "python-docx",
    "jwt": "pyjwt",
    "dateutil": "python-dateutil",
}

# Calls that open a database connection or engine.
RESOURCE_OPENERS = {"connect", "create_engine", "get_engine", "get_connection"}
CLOSING_METHODS = {"close", "dispose"}


class ValidationFinding(ContractModel):
    check: Literal["imports_vs_requirements", "unclosed_resources", "docs_paths", "docs_names"]
    file: str
    line: int
    message: str


def normalize_package(name):
    """'SQLAlchemy>=2.0' -> 'sqlalchemy'; 'python_dotenv' -> 'python-dotenv'."""
    base = re.split(r"[<>=!~;\[\s]", name.strip(), maxsplit=1)[0]
    return base.lower().replace("_", "-")


# --- A. imports vs requirements --------------------------------------------

def top_level_imports(tree):
    """(module name, line) for every absolute import in a file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0], node.lineno


def check_imports_vs_requirements(path, tree, required_packages, local_roots):
    findings = []
    seen = set()
    for module, line in top_level_imports(tree):
        if module in seen or module in sys.stdlib_module_names or module in local_roots:
            continue
        seen.add(module)
        package = normalize_package(IMPORT_TO_PACKAGE.get(module, module))
        if package not in required_packages:
            findings.append(ValidationFinding(
                check="imports_vs_requirements", file=path, line=line,
                message=f"imports '{module}', but '{package}' is not in requirements.txt",
            ))
    return findings


# --- B. unclosed resources ---------------------------------------------------

def call_name(call):
    """'connect' for sqlite3.connect(...), 'get_engine' for get_engine(...)."""
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    if isinstance(function, ast.Name):
        return function.id
    return None


def target_key(node):
    """'engine' for engine, 'self.engine' for self.engine; None otherwise."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def is_sqlite_connect(call):
    function = call.func
    return (isinstance(function, ast.Attribute) and function.attr == "connect"
            and isinstance(function.value, ast.Name) and function.value.id == "sqlite3")


def sqlite_factories(tree):
    """Functions in this file that open and return a sqlite3 connection."""
    names = {"get_connection"}
    for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if any(isinstance(n, ast.Call) and is_sqlite_connect(n) for n in ast.walk(function)):
            names.add(function.name)
    return names


def check_unclosed_resources(path, tree):
    findings = []

    closed = set()      # keys with .close() / .dispose() anywhere in the file
    returned = set()    # keys returned (a factory hands the resource to its caller)
    in_with = set()     # keys used as a context manager: with key:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CLOSING_METHODS:
            key = target_key(node.func.value)
            if key:
                closed.add(key)
        elif isinstance(node, ast.Return) and node.value is not None:
            key = target_key(node.value)
            if key:
                returned.add(key)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                key = target_key(item.context_expr)
                if key:
                    in_with.add(key)

    factories = sqlite_factories(tree)

    for node in ast.walk(tree):
        # A connection stored in a variable must be closed, returned, or used in "with".
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and call_name(node.value) in RESOURCE_OPENERS:
            for target in node.targets:
                key = target_key(target)
                if key and key not in closed | returned | in_with:
                    findings.append(ValidationFinding(
                        check="unclosed_resources", file=path, line=node.lineno,
                        message=f"'{key}' is opened with {call_name(node.value)}() but never closed "
                                "(.close()/.dispose()); on Windows the database file stays locked",
                    ))

        # "with sqlite3.connect(...)" only commits; it does NOT close (case #12).
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and (is_sqlite_connect(expr) or call_name(expr) in factories):
                    findings.append(ValidationFinding(
                        check="unclosed_resources", file=path, line=node.lineno,
                        message=f"'with {call_name(expr)}(...)' only commits for sqlite3; "
                                "it does not close the connection",
                    ))
    return findings


# --- checks C and D: references in Markdown documents -------------------------

# Folders that are never part of the pilot's own content.
IGNORED_FOLDERS = {".git", ".venv", "node_modules", "dist", "__pycache__", "outputs"}

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
# A path: no spaces, and either a folder part ("pilot/app") or a file ending (".py").
PATH_LIKE = re.compile(r"^[\w.\-/]+$")
FILE_ENDING = re.compile(r"\.[A-Za-z]{1,5}$")
# Code names: get_engine(), ExamSession, Base.metadata, Base.metadata.create_all()
CALL_NAME = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*\(\)$")
DOTTED_NAME = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
CLASS_NAME = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$")


def pilot_files(pilot_root):
    """Relative paths of the pilot's own files and folders (without generated ones)."""
    pilot_root = Path(pilot_root)
    found = set()
    for path in pilot_root.rglob("*"):
        relative = path.relative_to(pilot_root)
        if IGNORED_FOLDERS & set(relative.parts):
            continue
        found.add(relative.as_posix())
    return found


def defined_names(pilot_root, files):
    """Every class, function, assigned, imported or attribute name in the pilot's Python code."""
    names = set()
    for relative in files:
        if not relative.endswith(".py"):
            continue
        try:
            tree = ast.parse((Path(pilot_root) / relative).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                # Also attributes the code only uses, such as Base.metadata.create_all:
                # they come from a library, and a document may name them correctly.
                names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
                    names.add(alias.name.split(".")[-1])
    return names


def inline_code_spans(text):
    """(line number, `code` text) outside fenced code blocks such as Mermaid diagrams."""
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            for match in INLINE_CODE.finditer(line):
                yield number, match.group(1).strip()


def looks_like_path(token):
    # "/api/v1/exams" is an API route, not a file; "https://..." is a link.
    if token.startswith("/") or "://" in token or not PATH_LIKE.match(token):
        return False
    return "/" in token or bool(FILE_ENDING.search(token))


def check_markdown_references(relative, text, files, names):
    """Checks C and D for one Markdown document."""
    basenames = {PurePosixPath(path).name for path in files}
    findings = []

    for line, token in inline_code_spans(text):
        if CALL_NAME.match(token) or DOTTED_NAME.match(token) or CLASS_NAME.match(token):
            # For Base.metadata.create_all() the first and last parts must exist.
            parts = token.removesuffix("()").split(".")
            # A dotted name with a file ending (database.py) is a path, handled below.
            if not (DOTTED_NAME.match(token) and FILE_ENDING.search(token) and looks_like_path(token)):
                missing = [part for part in {parts[0], parts[-1]} if part not in names]
                if missing:
                    findings.append(ValidationFinding(
                        check="docs_names", file=relative, line=line,
                        message=f"`{token}`: {', '.join(sorted(missing))} is not found in the pilot's Python code",
                    ))
                continue

        if looks_like_path(token):
            clean = token.rstrip("/")
            exists = clean in files if "/" in clean else clean in basenames
            if not exists:
                findings.append(ValidationFinding(
                    check="docs_paths", file=relative, line=line,
                    message=f"`{token}` does not exist in the pilot workspace",
                ))
    return findings


# --- running the checks -------------------------------------------------------

def validate_files(pilot_root, changed_paths):
    """Run all checks on the changed Python and Markdown files of the pilot workspace."""
    pilot_root = Path(pilot_root)
    required = {normalize_package(item) for item in read_requirements(pilot_root / "requirements.txt")}
    # Top-level folders and modules of the pilot itself are local, not packages.
    local_roots = {entry.stem if entry.suffix == ".py" else entry.name for entry in pilot_root.iterdir()}

    findings = []
    markdown = [relative for relative in sorted(changed_paths) if relative.endswith(".md")]
    if markdown:
        files = pilot_files(pilot_root)
        names = defined_names(pilot_root, files)
        for relative in markdown:
            path = pilot_root / relative
            if path.is_file():
                findings += check_markdown_references(relative, path.read_text(encoding="utf-8"), files, names)

    for relative in sorted(changed_paths):
        path = pilot_root / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            findings.append(ValidationFinding(
                check="imports_vs_requirements", file=relative, line=error.lineno or 0,
                message=f"could not be parsed: {error.msg}",
            ))
            continue
        findings += check_imports_vs_requirements(relative, tree, required, local_roots)
        findings += check_unclosed_resources(relative, tree)
    return findings
