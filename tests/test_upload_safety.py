"""Upload hardening: reject path-traversal filenames and symlink escapes."""

import os
import pytest

from app.core.upload_safety import (
    normalize_filename,
    is_within_base,
    safe_copy_no_symlink,
    PathTraversalError,
)


def test_normalize_strips_directory_components():
    assert normalize_filename("a/b/c.txt") == "c.txt"
    assert normalize_filename("..\\..\\evil.sh") == "evil.sh"


def test_normalize_rejects_empty_or_dotonly():
    with pytest.raises(ValueError):
        normalize_filename("")
    with pytest.raises(ValueError):
        normalize_filename("..")
    with pytest.raises(ValueError):
        normalize_filename(".")


def test_normalize_keeps_safe_name():
    assert normalize_filename("report_2024.pdf") == "report_2024.pdf"


def test_is_within_base_true(tmp_path):
    child = tmp_path / "sub" / "f.txt"
    assert is_within_base(child, tmp_path) is True


def test_is_within_base_false_for_escape(tmp_path):
    outside = tmp_path.parent / "other.txt"
    assert is_within_base(outside, tmp_path) is False


def test_safe_copy_plain_file(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    out = safe_copy_no_symlink(src, dest_dir / "src.txt")
    assert out.read_text(encoding="utf-8") == "hello"


def test_safe_copy_rejects_symlink_source(tmp_path):
    # Create a symlink pointing at a sensitive file; copy must refuse it.
    target = tmp_path / "secret.txt"
    target.write_text("TOPSECRET", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not permitted on this platform/run")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    with pytest.raises(PathTraversalError):
        safe_copy_no_symlink(link, dest_dir / "link.txt")


def test_safe_copy_rejects_dest_escape(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("x", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    # dest tries to escape the dest_dir base
    with pytest.raises(PathTraversalError):
        safe_copy_no_symlink(src, dest_dir / ".." / "escaped.txt", base=dest_dir)
