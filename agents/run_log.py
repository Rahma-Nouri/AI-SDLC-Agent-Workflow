"""Run log: every main.py command leaves a complete text log as evaluation evidence.

The log gets everything the console shows (stdout and stderr) and every
answer the human types at a gate. The console itself looks exactly the same.

How it works, for a beginner:
- sys.stdout and sys.stderr are replaced by a "tee": every text goes to the
  console first, then (with secrets removed) to the log file.
- sys.stdin is replaced by a recorder: each line the human types is passed on
  unchanged and also written to the log.
- Python's input() writes its prompt to sys.stdout and reads sys.stdin, so the
  log shows "prompt: answer" exactly as on screen. This works for every gate,
  also the ones that got `ask=input` as a default argument long before.

The log folder is outside both repositories (World 1 and the pilot), so a log
can never be committed by accident: ../AI-SDLC-run-logs/ by default, or the
folder in the environment variable RUN_LOG_DIR.
"""

import io
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_LOG_FOLDER_NAME = "AI-SDLC-run-logs"

# Environment variables whose values must never reach the log.
SECRET_NAMES = ("GEMINI_API_KEY", "GITHUB_TOKEN")
REDACTED = "[REDACTED]"


def run_log_folder(workflow_root):
    """RUN_LOG_DIR if it is set, otherwise AI-SDLC-run-logs next to the workflow folder."""
    configured = os.getenv("RUN_LOG_DIR")
    if configured:
        return Path(configured)
    return Path(workflow_root).resolve().parent / DEFAULT_LOG_FOLDER_NAME


def command_name(argv):
    """The command for the file name: "--backend-patch T2" -> "backend-patch"; no option -> "run"."""
    for argument in argv:
        if argument.startswith("--"):
            # Only letters, digits, "-" and "_" are kept, so the name is a safe file name.
            name = re.sub(r"[^A-Za-z0-9_-]", "", argument[2:].split("=")[0])
            if name:
                return name
    return "run"


class SecretFilter:
    """Replaces the values of the secret variables with [REDACTED]."""

    def __init__(self, env_file=None):
        # Values in the .env file are read once now; os.environ is read at every
        # write, because the agents load the .env file only later (load_dotenv).
        try:
            from_file = dotenv_values(env_file) if env_file else dotenv_values()
        except Exception:
            from_file = {}
        self.file_secrets = [from_file.get(name) for name in SECRET_NAMES]

    def __call__(self, text):
        secrets = {value for value in self.file_secrets + [os.environ.get(name) for name in SECRET_NAMES] if value}
        # Longest first, so a secret that contains another one is replaced whole.
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        return text


class _Tee(io.TextIOBase):
    """Writes to the console and to the log.

    It has no fileno() (io.TextIOBase raises an error there). That is on
    purpose: input() then writes its prompt through this object, so the
    prompt reaches the log too, instead of going straight to the console.
    """

    def __init__(self, console, log_file, redact):
        self.console = console
        self.log_file = log_file
        self.redact = redact

    def write(self, text):
        self.console.write(text)
        self.log_file.write(self.redact(text))
        return len(text)

    def flush(self):
        self.console.flush()
        self.log_file.flush()

    def isatty(self):
        return self.console.isatty()

    @property
    def encoding(self):
        return self.console.encoding

    @property
    def errors(self):
        return self.console.errors


class _InputRecorder(io.TextIOBase):
    """Passes the human's typed lines on unchanged and writes them to the log."""

    def __init__(self, console, log_file, redact):
        self.console = console
        self.log_file = log_file
        self.redact = redact

    def readable(self):
        return True

    def readline(self, size=-1):
        line = self.console.readline(size)
        # The console already shows the typed text; the log needs it written.
        # An empty string means the input ended (no human answer), so nothing to log.
        if line:
            self.log_file.write(self.redact(line if line.endswith("\n") else line + "\n"))
            self.log_file.flush()
        return line

    def isatty(self):
        return self.console.isatty()

    @property
    def encoding(self):
        return self.console.encoding


class RunLog:
    """Use as `with RunLog(command, folder): main()`.

    Inside the with block, console output and typed answers are copied to
    <folder>/<command>_<YYYYMMDD_HHMMSS>.log. Afterwards the real console
    streams are put back.
    """

    def __init__(self, command, folder, argv=None, env_file=None):
        self.command = command
        self.folder = Path(folder)
        self.argv = list(sys.argv[1:] if argv is None else argv)
        self.redact = SecretFilter(env_file)
        self.path = None

    def _new_path(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.folder / f"{self.command}_{stamp}.log"
        number = 2
        # Two runs in the same second must not share (or overwrite) one log.
        while path.exists():
            path = self.folder / f"{self.command}_{stamp}_{number}.log"
            number += 1
        return path

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self._new_path()
        # buffering=1: the file is written line by line, so a crash keeps the evidence.
        self.log_file = open(self.path, "x", encoding="utf-8", buffering=1)
        self.log_file.write(self.redact(
            f"# Run log: python main.py {' '.join(self.argv)}\n"
            f"# Started: {datetime.now().isoformat(timespec='seconds')}\n\n"
        ))
        self.saved = (sys.stdout, sys.stderr, sys.stdin)
        sys.stdout = _Tee(sys.stdout, self.log_file, self.redact)
        sys.stderr = _Tee(sys.stderr, self.log_file, self.redact)
        sys.stdin = _InputRecorder(sys.stdin, self.log_file, self.redact)
        return self

    def __exit__(self, error_type, error, error_traceback):
        sys.stdout, sys.stderr, sys.stdin = self.saved
        if error_type is not None and not issubclass(error_type, SystemExit):
            # Python prints this traceback on the console after we return; the log
            # gets its own copy now, because the tee is already gone by then.
            details = "".join(traceback.format_exception(error_type, error, error_traceback))
            self.log_file.write(self.redact("\n" + details))
        self.log_file.write(f"\n# Finished: {datetime.now().isoformat(timespec='seconds')}\n")
        self.log_file.close()
        return False  # never hide an error
