"""Noise filter must not drop a real DATA fact that contains a noise substring (W-8).

Context-arch audit (2026-07-03): _KEY_DATA_NOISE uses bare substring matching, so
"total revenue = 45000 USD" is dropped by the "total " token (meant for `ls -la`
"total 48K"), and "access token count = 12" by "token" (meant for TLS chatter). A
fact carrying a real result NUMBER must survive even if it also contains a noise
word — the number is the signal.
"""

from __future__ import annotations

from app.orchestration.context_ledger import ContextLedger


def test_ls_total_line_still_filtered():
    # The genuine noise it was designed for: an `ls` size header, no result meaning.
    assert ContextLedger._is_key_data_noise("total 48K") is True
    assert ContextLedger._is_key_data_noise("drwxr-xr-x 2 root root") is True


def test_financial_total_with_number_is_kept():
    assert ContextLedger._is_key_data_noise("total revenue = 45000 USD") is False
    assert ContextLedger._is_key_data_noise("total net electrical output 20 kW") is False


def test_token_count_fact_is_kept():
    assert ContextLedger._is_key_data_noise("access token count = 12 across 3 services") is False


def test_tls_chatter_still_filtered():
    assert ContextLedger._is_key_data_noise("TLS handshake using cipher ECDHE") is True
    assert ContextLedger._is_key_data_noise("ALPN: h2 negotiated") is True


def test_plain_finding_kept():
    assert ContextLedger._is_key_data_noise("CGE = 62.93% forward-computed") is False
