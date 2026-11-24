"""Runs one repo function on a batch of inputs. Executed by file path inside the OS sandbox."""
import copy
import importlib
import io
import os
import pickle
import signal
import sys

_MAX_MEMORY = 2 << 30
_SAFE_BUILTINS = {"complex"}  # bytearray needs no global under protocol 5


class SafeUnpickler(pickle.Unpickler):
    # Parent unpickles child output outside the sandbox: allow plain data only, never repo classes.
    def find_class(self, module, name):
        if module == "builtins" and name in _SAFE_BUILTINS:
            return getattr(__import__("builtins"), name)
        raise pickle.UnpicklingError(f"forbidden {module}.{name}")


def safe_loads(data: bytes):
    return SafeUnpickler(io.BytesIO(data)).load()


class _Timeout(BaseException):
    pass


def _alarm(signum, frame):
    raise _Timeout


def _call(fn, args):
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        return ("return", fn(*args), args)
    except _Timeout:
        return ("timeout",)
    except BaseException as e:
        return ("raise", type(e).__qualname__, args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _checked(outcome):
    try:
        data = pickle.dumps(outcome, protocol=5)
        safe_loads(data)
        return outcome
    except Exception:
        return ("error", "unpicklable")


def main() -> None:
    out = os.fdopen(os.dup(1), "wb")
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)  # keep the function's prints out of the result pipe
    repo_root, module, qualname, inputs = pickle.load(sys.stdin.buffer)
    if sys.platform.startswith("linux"):  # macOS does not enforce RLIMIT_AS
        import resource
        hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        cap = _MAX_MEMORY if hard == resource.RLIM_INFINITY else min(_MAX_MEMORY, hard)
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))  # before any repo code is imported
    sys.path[:1] = [repo_root, os.path.join(repo_root, "src")]  # drop this script's own dir
    fn = importlib.import_module(module)
    for part in qualname.split("."):
        fn = getattr(fn, part)
    signal.signal(signal.SIGALRM, _alarm)
    outcomes = [_checked(_call(fn, copy.deepcopy(args))) for args in inputs]
    out.write(pickle.dumps(outcomes, protocol=5))
    out.flush()


if __name__ == "__main__":
    main()
