"""MCP config hot-reload: diff current vs desired servers, mtime-gated reload."""

from app.mcp.config_reload import diff_server_configs, MCPConfigWatcher


class _Cfg:
    """Minimal stand-in for MCPServerConfig (name + identity fields)."""
    def __init__(self, name, command="", args=None, url=None, enabled=True):
        self.name = name
        self.command = command
        self.args = args or []
        self.url = url
        self.enabled = enabled


def test_diff_added_servers():
    current = [_Cfg("a")]
    desired = [_Cfg("a"), _Cfg("b")]
    d = diff_server_configs(current, desired)
    assert d.added == ["b"]
    assert d.removed == []
    assert d.changed == []


def test_diff_removed_servers():
    current = [_Cfg("a"), _Cfg("b")]
    desired = [_Cfg("a")]
    d = diff_server_configs(current, desired)
    assert d.removed == ["b"]
    assert d.added == []


def test_diff_changed_server_command():
    current = [_Cfg("a", command="npx", args=["old"])]
    desired = [_Cfg("a", command="npx", args=["new"])]
    d = diff_server_configs(current, desired)
    assert d.changed == ["a"]


def test_diff_disabled_treated_as_removed():
    current = [_Cfg("a", enabled=True)]
    desired = [_Cfg("a", enabled=False)]
    d = diff_server_configs(current, desired)
    assert "a" in d.removed


def test_diff_no_change():
    current = [_Cfg("a", command="npx", args=["x"], url=None)]
    desired = [_Cfg("a", command="npx", args=["x"], url=None)]
    d = diff_server_configs(current, desired)
    assert d.added == [] and d.removed == [] and d.changed == []


def test_watcher_mtime_gate(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text('[]', encoding="utf-8")
    w = MCPConfigWatcher(str(f))
    # First check after init: file exists, mtime unseen → changed.
    assert w.changed() is True
    # No write → not changed.
    assert w.changed() is False


def test_watcher_missing_file_not_changed(tmp_path):
    w = MCPConfigWatcher(str(tmp_path / "nope.json"))
    assert w.changed() is False
