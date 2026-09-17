from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_e2e_conftest():
    path = Path(__file__).parent / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_lilith_tools_e2e_conftest_scope_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeItem:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fspath = path
        self.markers: list[object] = []

    def add_marker(self, marker: object) -> None:
        self.markers.append(marker)


def _marker_names(item: _FakeItem) -> list[str]:
    names: list[str] = []
    for marker in item.markers:
        name = getattr(marker, "name", None)
        if name is None:
            mark = getattr(marker, "mark", None)
            name = getattr(mark, "name", None)
        if name is not None:
            names.append(str(name))
    return names


def test_collection_hook_does_not_mark_sibling_unit_tests(monkeypatch) -> None:
    conftest = _load_e2e_conftest()
    monkeypatch.setattr(sys, "argv", ["pytest"])
    unit_item = _FakeItem(Path(__file__))
    e2e_item = _FakeItem(Path(__file__).parent / "e2e" / "test_sample.py")

    conftest.pytest_collection_modifyitems(object(), [unit_item, e2e_item])

    assert _marker_names(unit_item) == []
    assert _marker_names(e2e_item) == ["e2e", "skip"]


def test_collection_hook_e2e_opt_in_keeps_sibling_unit_unmarked(monkeypatch) -> None:
    conftest = _load_e2e_conftest()
    monkeypatch.setattr(sys, "argv", ["pytest", "-m", "e2e"])
    unit_item = _FakeItem(Path(__file__))
    e2e_item = _FakeItem(Path(__file__).parent / "e2e" / "test_sample.py")

    conftest.pytest_collection_modifyitems(object(), [unit_item, e2e_item])

    assert _marker_names(unit_item) == []
    assert _marker_names(e2e_item) == ["e2e"]
