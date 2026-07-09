"""Crash recovery must be MANUAL by default: no auto-resume on startup.

We decouple auto-resume (startup re-runs tasks) from task_resume (checkpoint
persistence + the manual /resume button). The default is checkpoints ON,
auto-resume OFF — so a restarted server surfaces a "Tiếp tục" button instead of
silently re-spending tokens.
"""

from __future__ import annotations

import inspect

from app import main as app_main
from app.config import get_settings


def test_auto_resume_defaults_off_but_checkpoints_on():
    s = get_settings()
    assert s.enable_task_auto_resume is False
    assert s.enable_task_resume is True


def test_startup_gates_auto_resume_on_new_flag():
    src = inspect.getsource(app_main.lifespan)
    assert "enable_task_auto_resume" in src
