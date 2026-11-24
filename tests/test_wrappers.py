import pytest

from slopgate.wrappers import find_wrappers, format_wrapper


def test_forwarding_accessor_with_no_callers_is_noted():
    w, = find_wrappers({("m.py", "C.get_record")}, {"m.py": "class C:\n    def get_record(self, k):\n        return self._records.get(k)\n"})
    assert format_wrapper(w) == "note: m.py:2 get_record only forwards to self._records.get and has at most one call site found by name."


def test_called_twice_is_not_noted():
    assert find_wrappers({("m.py", "w")}, {"m.py": "def w(x): return g(x)\n", "a.py": "w(1)\nw(2)\n"}) == []


def test_changed_arguments_are_not_forwarding():
    assert find_wrappers({("m.py", "w")}, {"m.py": "def w(x): return g(x + 1)\n"}) == []


@pytest.mark.parametrize("src", [
    "__all__ = ['w']\ndef w(x): return g(x)\n",
    "@app.get('/')\ndef w(x): return g(x)\n",
    "def __call__(self, x): return self.f(x)\n",
])
def test_exclusions(src):
    assert find_wrappers({("m.py", "w"), ("m.py", "__call__")}, {"m.py": src}) == []


def test_async_await_forwarding_and_test_path_and_syntax_error():
    assert len(find_wrappers({("m.py", "w")}, {"m.py": "async def w(x):\n    'doc'\n    return await g(x)\n", "bad.py": "def ("})) == 1
    assert find_wrappers({("tests/t.py", "w")}, {"tests/t.py": "def w(x): return g(x)\n"}) == []


def test_reordered_arguments_are_not_forwarding():
    assert find_wrappers({("m.py", "w")}, {"m.py": "def w(a, b): return g(b, a)\n"}) == []


def test_keyword_forwarding_by_name_is_noted():
    assert len(find_wrappers({("m.py", "w")}, {"m.py": "def w(a, b): return g(a, b=b)\n"})) == 1


def test_subclass_methods_are_skipped():
    body = "    def forward(self, x):\n        return self.net(x)\n"
    assert find_wrappers({("m.py", "M.forward")}, {"m.py": "import torch.nn as nn\nclass M(nn.Module):\n" + body}) == []
    assert len(find_wrappers({("m.py", "M.forward")}, {"m.py": "class M:\n" + body})) == 1
    assert len(find_wrappers({("m.py", "M.forward")}, {"m.py": "class M(object):\n" + body})) == 1


def test_repo_is_not_parsed_when_no_candidate_forwards(monkeypatch):
    import slopgate.wrappers
    parsed, real = [], slopgate.wrappers._parse
    monkeypatch.setattr("slopgate.wrappers._parse", lambda texts: parsed.extend(texts) or real(texts))
    texts = {"m.py": "def w(x): return g(x + 1)\n", "a.py": "def h(): pass\n", "b.py": "w(1)\n"}
    assert find_wrappers({("m.py", "w")}, texts) == []
    assert parsed == ["m.py"]
