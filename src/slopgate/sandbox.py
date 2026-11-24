import os
import pickle
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from slopgate.child import safe_loads
from slopgate.index import FunctionInfo

Outcome = tuple
_CHILD = str(Path(__file__).with_name("child.py"))
_BATCH_TIMEOUT = 10


def sandbox_kind() -> Literal["sandbox-exec", "bwrap", "none"]:
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return "none"


def pick_interpreter(repo: Path) -> str:
    venv = repo / ".venv/bin/python"
    return str(venv) if venv.exists() else sys.executable


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


def run_batch(repo: Path, fn: FunctionInfo, inputs: list[tuple], interpreter: str) -> list[Outcome] | None:
    payload = pickle.dumps((str(repo.resolve()), fn.module, fn.qualname, inputs))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory() as tmp:
        workdir = os.path.realpath(tmp)  # sandbox-exec matches real paths (/var -> /private/var)
        proc = subprocess.Popen(_command(workdir, interpreter), cwd=workdir, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            out, _ = proc.communicate(payload, timeout=_BATCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None
        finally:
            # kill the whole group so a forked grandchild can't keep the pipe open or outlive the batch
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:  # group already gone (ESRCH, or EPERM on macOS when only zombies remain)
                pass
            proc.wait()
    if proc.returncode != 0:
        return None
    try:
        return safe_loads(out)
    except Exception:
        return None
