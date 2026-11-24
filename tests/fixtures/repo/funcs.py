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


def hash_mod_copy(s: str) -> int:
    return hash(s) % 1000003


def square_loop(xs: list[int]) -> list[int]:
    out = []
    for x in xs:
        out.append(x * x)
    return out


def square_pow(xs: list[int]) -> list[int]:
    return [x ** 2 for x in xs]


def abs_mine(x: int) -> int:
    return x if x >= 0 else -x


def abs_wrong_at_zero(x: int) -> int:
    if x == 0:
        return 1
    return x if x > 0 else -x


def raise_unless_zero_a(x: int) -> int:
    if x != 0:
        raise ValueError("nonzero")
    return 0


def raise_unless_zero_b(x: int) -> int:
    if x:
        raise ValueError(x)
    return x


def reads_file(path: str) -> str:
    return open(path).read()


def reads_file_copy(path: str) -> str:
    with open(path) as f:
        return f.read()


def ident_list(xs: list[int]) -> list[int]:
    return xs


def copy_list(xs: list[int]) -> list[int]:
    return list(xs)


def add_k(x: int, k: int = 0) -> int:
    return x + k


def ident_int(x: int) -> int:
    return x


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0) if x == x else x
