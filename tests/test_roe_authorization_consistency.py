"""ROE header/footer authorization posture must be CONSISTENT (task 3c44a93f #5b).

The pentest ROE template opened with a hard-coded "This is an authorized
security assessment." while the footer (_build_authorization_block) could
simultaneously say "Standing authorization does NOT cover it — proceed only with
per-target proof." One file both granting and revoking authorization is the
split-brain that made the agent oscillate attack/no-attack (Turn 5).

The fix computes the authorization posture ONCE and both header and footer
reflect it:
- in-scope (target IS in pentest_scope.yaml, e.g. a partner pentesting their own
  site): the ROE must clearly state the engagement is authorized and the full
  assessment may proceed per the contract — never hedge it into a refusal.
- out-of-scope public infra: the header must NOT assert blanket authorization;
  it must align with the footer's per-target-proof requirement.
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
          document: "Hợp đồng PLN-SEC-2026/06 ngày 22/06/2026"
          targets: ["pln.co.id", "*.pln.co.id"]
    """), encoding="utf-8")
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(f))
    ps.reload_scope()
    yield f
    monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
    ps.reload_scope()


def _pentest_roe(target_desc: str) -> str:
    # Render the pentest ROE synchronously via the private renderer path used by
    # generate_roe for an explicitly-classified pentest.
    target = roe_generator._extract_target(target_desc)
    return roe_generator._render_pentest_roe(
        target, "task1234", "Red Team (Full Scope)", "Full compromise demonstration"
    )


def test_in_scope_target_header_affirms_authorized_engagement(standing_on, scope_with_pln):
    """Partner pentesting their OWN in-scope site: ROE must affirm it's authorized
    and the assessment may proceed — not hedge it into needing more proof."""
    roe = _pentest_roe("Red team assessment of https://www.pln.co.id")
    low = roe.lower()
    # Affirms authorization and proceeding with the real engagement.
    assert "authorized" in low
    assert ("proceed" in low and "assessment" in low)
    # The contradicting per-target-proof / not-covered language must be ABSENT
    # for an in-scope target (that was the split-brain).
    assert "does not cover" not in low
    assert "proceed only with per-target proof" not in low


def test_out_of_scope_public_header_does_not_assert_blanket_authorization(
    standing_on, scope_with_pln
):
    """google.com is NOT in scope → the header must not unconditionally declare
    'This is an authorized security assessment'; it must align with the footer's
    per-target-proof requirement (no header/footer contradiction)."""
    roe = _pentest_roe("Red team assessment of https://www.google.com")
    low = roe.lower()
    # Footer still demands proof for out-of-scope public infra.
    assert "per-target proof" in low or "does not cover" in low
    # The header's unconditional grant must be gone (no split-brain): the file
    # must not BOTH demand proof AND open with a flat 'this is an authorized
    # security assessment' grant.
    assert "this is an authorized security assessment." not in low


def test_in_scope_and_out_of_scope_headers_differ(standing_on, scope_with_pln):
    """Sanity: the posture actually changes the header text between the two."""
    in_scope = _pentest_roe("pentest of www.pln.co.id")
    out_scope = _pentest_roe("pentest of www.google.com")
    # The two ROEs must not share an identical authorization statement.
    assert in_scope.split("## Target Scope")[0] != out_scope.split("## Target Scope")[0]
