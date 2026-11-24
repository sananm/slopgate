import pytest

from slopgate.deps import find_new_deps, format_new_dep


def test_third_party_import_never_seen_before_is_noted():
    d, = find_new_deps({"a.py"}, {"a.py": "import os\nimport polars as pl\n"}, {"a.py": "import os\n"})
    assert format_new_dep(d) == "note: a.py:2 imports a new dependency `polars`\n  that this repo has never used before."


def test_dependency_already_used_elsewhere_is_not_new():
    assert find_new_deps({"b.py"}, {"a.py": "import numpy\n", "b.py": "import numpy\n"}, {"a.py": "import numpy\n"}) == []


@pytest.mark.parametrize("src", ["import json\n", "from . import x\n", "from mypkg import y\n", "from collections.abc import Mapping\n"])
def test_stdlib_relative_and_first_party_are_ignored(src):
    after = {"a.py": src, "src/mypkg/__init__.py": ""}
    assert find_new_deps({"a.py"}, after, {}) == []


def test_syntax_error_file_is_skipped():
    assert find_new_deps({"a.py"}, {"a.py": "import (\n"}, {}) == []
