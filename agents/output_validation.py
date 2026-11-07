"""Automatic checks for AI-generated code (P1.8).

Each check comes from a real failure seen in this project:

A. imports_vs_requirements: the agent's code imports a package that is not in
   the pilot's requirements.txt (failure cases #5, #15).
B. unclosed_resources: a database connection or engine is opened but never
   closed, which breaks on Windows (failure cases #12 and #18 - seen twice).

The checks only WARN. They are simple rules (heuristics), so they can be
wrong; the human sees the warnings at H3 and makes the decision.
"""

import ast
import re
import sys
from pathlib import Path
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
    check: Literal["imports_vs_requirements", "unclosed_resources"]
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


# --- running the checks -------------------------------------------------------

def validate_files(pilot_root, changed_paths):
    """Run all checks on the changed Python files of the pilot workspace."""
    pilot_root = Path(pilot_root)
    required = {normalize_package(item) for item in read_requirements(pilot_root / "requirements.txt")}
    # Top-level folders and modules of the pilot itself are local, not packages.
    local_roots = {entry.stem if entry.suffix == ".py" else entry.name for entry in pilot_root.iterdir()}

    findings = []
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
