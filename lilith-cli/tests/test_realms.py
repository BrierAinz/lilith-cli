"""Tests for the Lilith IDE Realm / persistent project memory system."""

from __future__ import annotations

from pathlib import Path

from lilith_cli.ide.realms import Realm, RealmManager


class TestRealm:
    """Unit tests for the Realm data model."""

    def test_remember_and_forget(self):
        realm = Realm(name="test", root=Path("/tmp"))
        realm.remember("use pytest")
        assert "use pytest" in realm.memories
        assert realm.forget("use pytest")
        assert "use pytest" not in realm.memories

    def test_forget_by_index(self):
        realm = Realm(name="test", root=Path("/tmp"))
        realm.remember("a")
        realm.remember("b")
        assert realm.forget("1")
        assert realm.memories == ["b"]

    def test_important_files_and_standards(self):
        realm = Realm(name="test", root=Path("/tmp"))
        realm.add_important_file("src/main.py")
        realm.add_standard("snake_case")
        assert "src/main.py" in realm.important_files
        assert "snake_case" in realm.standards

    def test_to_dict_roundtrip(self):
        realm = Realm(name="x", root=Path("/tmp"))
        realm.remember("m")
        realm.add_important_file("f.py")
        data = realm.to_dict()
        restored = Realm.from_dict(data)
        assert restored.name == "x"
        assert restored.memories == ["m"]
        assert restored.important_files == ["f.py"]


class TestRealmManager:
    """Unit tests for RealmManager persistence."""

    def test_load_creates_realm(self, tmp_path):
        mgr = RealmManager(tmp_path)
        realm = mgr.load()
        assert realm.name == tmp_path.name
        assert realm.root == tmp_path

    def test_save_and_load(self, tmp_path):
        mgr = RealmManager(tmp_path)
        realm = mgr.load()
        realm.remember("important")
        mgr.save()

        mgr2 = RealmManager(tmp_path)
        realm2 = mgr2.load()
        assert "important" in realm2.memories

    def test_build_knowledge_prompt(self, tmp_path):
        mgr = RealmManager(tmp_path)
        realm = mgr.load()
        realm.add_standard("use type hints")
        realm.remember("backend is FastAPI")
        prompt = mgr.build_knowledge_prompt()
        assert "use type hints" in prompt
        assert "backend is FastAPI" in prompt

    def test_auto_index(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hi", encoding="utf-8")
        mgr = RealmManager(tmp_path)
        mgr.auto_index()
        realm = mgr.load()
        assert "README.md" in realm.important_files
