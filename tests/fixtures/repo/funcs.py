def add_one(x):
    return x + 1


def div(a, b):
    return a / b


def append_x(xs):
    xs.append("x")


def spin(n):
    while True:
        n += 1


def _write(path):
    with open(path, "w") as f:
        f.write("escaped")


def sneaky_write(path):
    _write(path)


def hash_mod(s: str) -> int:
    return hash(s) % 1000003
