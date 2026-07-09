"""ROE must carry the authorizing CREDENTIALS up-front (task 3c44a93f follow-up).

The worker agent — the one that actually runs offensive tools — never reads
pentest_scope.yaml and only sees roe_context[:7000] (CONTRACT + the HEAD of
ROE.md). The in-scope authorization statement said "see Authorization Reference
below", but that reference (the org + signed document number) lives near the END
of the ROE (~char 13k) and is truncated away. The agent saw an unbacked claim
and could still doubt it — exactly the Turn-5 refusal.

Fix: the in-scope header itself names the authorizing ORG and the engagement
DOCUMENT (pulled from the scope entry), so the agent reads a self-contained,
credible authorization at the very top — no need to read the scope file and no
dependency on the truncated tail.
"""

from __future__ import annotations

import textwrap
import types

import pytest

from app.security import roe_generator


@pytest.fixture
def standing_on(monkeypatch):
    import app.config
    monkeypatch.setattr(
        app.config, "get_settings",
        lambda: types.SimpleNamespace(
            roe_standing_authorization=True,
            roe_authorizing_org="National Cyber Security Operations",
            roe_authorization_basis="Standing government engagement mandate",
        ),
    )


@pytest.fixture
def scope_with_pln(tmp_path, monkeypatch):
    from app.security import pentest_scope as ps
    f = tmp_path / "pentest_scope.yaml"
    f.write_text(textwrap.dedent("""
        - org: "PT PLN (Persero)"
          document: "Hợp đồng kiểm thử thâm nhập số PLN-SEC-2026/06 ký ngày 22/06/2026"
          targets: ["pln.co.id", "*.pln.co.id"]
    """), encoding="utf-8")
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(f))
    ps.reload_scope()
    yield f
    monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
    ps.reload_scope()


def test_in_scope_header_names_org_and_document(standing_on, scope_with_pln):
    """The header authorization statement must cite the real org + document."""
    stmt = roe_generator._authorization_statement("www.pln.co.id")
    assert "PT PLN (Persero)" in stmt
    assert "PLN-SEC-2026/06" in stmt


def test_credentials_survive_roe_head_truncation(standing_on, scope_with_pln):
    """The org + document must appear within the first 7000 chars of the rendered
    ROE — the slice the worker actually sees (turn_executor roe_context[:7000])."""
    roe = roe_generator._render_pentest_roe(
        "www.pln.co.id", "task1234", "Red Team (Full Scope)", "Full compromise"
    )
    head = roe[:7000]
    assert "PT PLN (Persero)" in head
    assert "PLN-SEC-2026/06" in head


def test_out_of_scope_header_has_no_fabricated_credentials(standing_on, scope_with_pln):
    """An out-of-scope target must NOT get a fabricated org/document grant."""
    stmt = roe_generator._authorization_statement("www.google.com")
    assert "PT PLN" not in stmt
    assert "PLN-SEC-2026/06" not in stmt


def test_self_attested_when_no_scope_entry(standing_on, monkeypatch, tmp_path):
    """No scope match and not public infra → self-attested, no invented document."""
    from app.security import pentest_scope as ps
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(empty))
    ps.reload_scope()
    try:
        stmt = roe_generator._authorization_statement("10.0.0.5")
        assert "PLN-SEC" not in stmt
    finally:
        monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
        ps.reload_scope()
