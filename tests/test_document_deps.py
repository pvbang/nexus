"""Verify document-rendering dependencies declared & importable (skips if absent)."""
import tomllib
from pathlib import Path


def test_pyproject_declares_render_deps():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(data["project"]["dependencies"])
    for pkg in ("markdown-it-py", "weasyprint", "mdit-py-plugins", "pypandoc"):
        assert pkg in deps, f"{pkg} missing from pyproject dependencies"
