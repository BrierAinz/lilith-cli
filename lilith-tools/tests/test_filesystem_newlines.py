from lilith_tools.filesystem import BatchEditTool, FileEditTool, FileWriteTool


def test_file_write_preserves_explicit_lf_bytes(tmp_path) -> None:
    target = tmp_path / "write.txt"
    result = FileWriteTool().execute(path=str(target), content="A\n")
    assert result.success
    assert target.read_bytes() == b"A\n"


def test_file_edit_does_not_translate_newlines(tmp_path) -> None:
    target = tmp_path / "edit.txt"
    target.write_bytes(b"A\nB\n")
    result = FileEditTool().execute(
        path=str(target), old_string="A", new_string="X"
    )
    assert result.success
    assert target.read_bytes() == b"X\nB\n"


def test_batch_edit_preserves_explicit_lf_bytes(tmp_path) -> None:
    target = tmp_path / "batch.txt"
    target.write_bytes(b"A\nB\n")
    result = BatchEditTool().execute(
        edits=[{
            "path": str(target),
            "old_string": "B",
            "new_string": "Y",
        }],
        preview=False,
    )
    assert result.success
    assert target.read_bytes() == b"A\nY\n"
