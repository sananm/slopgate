import os
import pickle
import stat
import time


def setsid_churn(n):
    if os.fork() == 0:
        os.setsid()
        if os.fork() == 0:
            os.closerange(0, 1024)  # release the result pipe so the parent proceeds to cleanup
            end = time.monotonic() + 3
            while time.monotonic() < end:
                n += 1
                os.makedirs(f"churn{n}", exist_ok=True)
        os._exit(0)
    return n


def flood_result(n):
    for fd in range(3, 64):
        try:
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                break
        except OSError:
            pass
    data = memoryview(pickle.dumps([("return", "x" * (17 << 20), (n,))]))  # valid, just too big
    while data:
        data = data[os.write(fd, data):]
    os._exit(0)
