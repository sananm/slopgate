import os
import pickle
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

from slopgate.child import safe_loads
from slopgate.index import FunctionInfo

Outcome = tuple
_CHILD = str(Path(__file__).with_name("child.py"))
_BATCH_TIMEOUT = 10
_MAX_OUTPUT = 16 << 20


def sandbox_kind() -> Literal["sandbox-exec", "bwrap", "none"]:
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return "none"


def pick_interpreter(repo: Path) -> str:
    venv = repo / ".venv/bin/python"
    if venv.exists():
        return str(venv)
    active = os.environ.get("VIRTUAL_ENV")
    if active and (Path(active) / "bin/python").exists():
        return str(Path(active) / "bin/python")
    return shutil.which("python3") or sys.executable


def _command(workdir: str, interpreter: str) -> list[str]:
    kind = sandbox_kind()
    if kind == "sandbox-exec":
        profile = (
            "(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n"
            f'(allow file-write* (subpath "{workdir}") (literal "/dev/null"))\n'
        )
        return ["sandbox-exec", "-p", profile, interpreter, _CHILD]
    if kind == "bwrap":
        return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--bind", workdir, workdir, "--unshare-net", "--die-with-parent", interpreter, _CHILD]
    return [interpreter, _CHILD]


def _read_bounded(proc: subprocess.Popen, timeout: float) -> bytearray | None:
    """Child stdout, or None past the batch deadline or over _MAX_OUTPUT (never holds more than cap + 1 bytes)."""
    deadline = time.monotonic() + timeout
    fd, buf = proc.stdout.fileno(), bytearray()
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            return None
        chunk = os.read(fd, min(1 << 16, _MAX_OUTPUT + 1 - len(buf)))
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_OUTPUT:
            return None
    try:
        proc.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return None
    return buf


def run_batch(repo: Path, fn: FunctionInfo, inputs: list[tuple], interpreter: str,
              timeout: float = _BATCH_TIMEOUT) -> list[Outcome] | None:
    payload = pickle.dumps((str(repo.resolve()), fn.module, fn.qualname, inputs))
    # never pass PYTHONHASHSEED: per-process hash randomization is how nondeterminism is detected
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"} | {"PYTHONDONTWRITEBYTECODE": "1"}
    # an escaped setsid grandchild may still be writing in the workdir: cleanup must never raise
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, tempfile.TemporaryFile() as stdin:
        stdin.write(payload)  # a file, not a pipe: a child that never reads stdin can't block the parent
        stdin.seek(0)
        workdir = os.path.realpath(tmp)  # sandbox-exec matches real paths (/var -> /private/var)
        proc = subprocess.Popen(_command(workdir, interpreter), cwd=workdir, env=env, stdin=stdin,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            out = _read_bounded(proc, timeout)
            if out is None:
                return None
        finally:
            # kill the whole group so a forked grandchild can't keep the pipe open or outlive the batch
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:  # group already gone (ESRCH, or EPERM on macOS when only zombies remain)
                pass
            proc.wait()
            proc.stdout.close()
    if proc.returncode != 0:
        return None
    try:
        return safe_loads(out)
    except Exception:
        return None
