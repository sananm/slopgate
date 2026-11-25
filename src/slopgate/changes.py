from dataclasses import dataclass, field

from slopgate.index import FunctionInfo


@dataclass
class Changes:
    new: list[FunctionInfo] = field(default_factory=list)
    changed: list[FunctionInfo] = field(default_factory=list)
    existing: list[FunctionInfo] = field(default_factory=list)


def classify(before: list[FunctionInfo], after: list[FunctionInfo]) -> Changes:
    old = {(f.module, f.qualname): f.body_key for f in before}
    now = {(f.module, f.qualname): f.body_key for f in after}
    # a body is "moved" only if none of its original locations still holds it unchanged
    kept = {k for loc, k in old.items() if now.get(loc) == k}
    old_bodies = {f.body_key for f in before} - kept
    ch = Changes()
    for f in after:
        prev = old.get((f.module, f.qualname))
        if prev is None:
            (ch.existing if f.body_key in old_bodies else ch.new).append(f)
        elif prev != f.body_key:
            ch.changed.append(f)
        else:
            ch.existing.append(f)
    return ch
