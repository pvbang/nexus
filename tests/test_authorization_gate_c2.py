"""Tests for the live-offensive authorization gate (audit Gói 3: C2).

The system accepted a task to escalate privileges and "control the entire system"
against a real public IP and executed live commands with NO authorization check.
This gate blocks live OFFENSIVE ops against REAL PUBLIC infrastructure unless the
owner explicitly opts in — while leaving lab/CTF/private targets, OSINT, and
non-security tasks (e.g. product design) completely unaffected.

Classification is FORMAT-based (ipaddress / regex), never semantic keyword matching.
"""

from __future__ import annotations

import pytest

from app.security.authorization_gate import (
    AuthorizationDecision,
    TargetClass,
    classify_target,
    detect_offensive_intent,
    evaluate_authorization,
    is_live_offensive_authorized,
    requires_authorization,
)
from app.orchestration.pipeline import NexusExecutionPipeline


# ───────────────────────── classify_target ─────────────────────────


def test_classify_real_public_ip():
    cls, target = classify_target("leo thang đặc quyền qua target ip 117.5.141.7")
    assert cls is TargetClass.REAL_PUBLIC_INFRA
    assert target == "117.5.141.7"


def test_classify_private_ip_is_safe():
    cls, _ = classify_target("pentest 192.168.1.10 on my LAN")
    assert cls is TargetClass.SAFE


def test_classify_loopback_is_safe():
    cls, _ = classify_target("attack 127.0.0.1:8080")
    assert cls is TargetClass.SAFE


def test_classify_lab_domain_is_safe():
    cls, _ = classify_target("exploit https://target.lab.example.com/api")
    assert cls is TargetClass.SAFE


def test_classify_example_domain_is_safe():
    cls, _ = classify_target("test rce on example.com")
    assert cls is TargetClass.SAFE


def test_classify_real_public_domain():
    cls, target = classify_target("full compromise of acme-corp-prod.net")
    assert cls is TargetClass.REAL_PUBLIC_INFRA
    assert target == "acme-corp-prod.net"


def test_classify_no_target_is_none():
    cls, _ = classify_target("Design a 20 kWe biomass gasifier generator")
    assert cls is TargetClass.NONE


# ── IPv6 targets must be gated too (review MINOR: only IPv4 dotted-decimal was seen) ──

def test_classify_public_ipv6_is_real():
    cls, target = classify_target("exploit 2001:4860:4860::8888 to gain a shell")
    assert cls is TargetClass.REAL_PUBLIC_INFRA
    assert target == "2001:4860:4860::8888"


def test_classify_public_ipv6_bracketed_with_port_is_real():
    cls, target = classify_target("rce on [2606:4700:4700::1111]:443")
    assert cls is TargetClass.REAL_PUBLIC_INFRA
    assert "2606:4700:4700::1111" in target


def test_classify_ipv6_loopback_is_safe():
    cls, _ = classify_target("attack ::1 on the local box")
    assert cls is TargetClass.SAFE


def test_classify_ipv6_link_local_is_safe():
    cls, _ = classify_target("scan fe80::1 interface")
    assert cls is TargetClass.SAFE


def test_classify_ipv6_unique_local_is_safe():
    cls, _ = classify_target("target fd00::1234 in the private mesh")
    assert cls is TargetClass.SAFE


def test_ipv6_does_not_false_positive_on_prose_colons():
    # "10:30" (a time) or "key:value" must not be read as an IPv6 target.
    cls, _ = classify_target("the meeting is at 10:30 about the design")
    assert cls is TargetClass.NONE


# ── Embedded-IPv4 tunnels must classify by the inner IPv4 (review CRITICAL) ──

def test_6to4_wrapping_public_ipv4_is_real():
    # 2002:7505:8d07::/48 embeds 117.5.141.7 — a public IPv4 → must be REAL, not
    # SAFE (ipaddress marks 2002::/16 is_private=True, which would wrongly pass).
    cls, _ = classify_target("exploit 2002:7505:8d07::1 to gain root")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_6to4_wrapping_private_ipv4_is_safe():
    # 2002 wrapping a private IPv4 (192.168.1.1 → 2002:c0a8:0101::) stays SAFE.
    cls, _ = classify_target("scan 2002:c0a8:0101::1 on the lan")
    assert cls is TargetClass.SAFE


def test_teredo_wrapping_public_ipv4_is_real():
    cls, _ = classify_target("rce on 2001:0:4136:e378:8000:63bf:3fff:fdd2")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_v6_mapped_public_ipv4_is_real():
    cls, _ = classify_target("attack ::ffff:8.8.8.8 server")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_v6_mapped_private_ipv4_is_safe():
    cls, _ = classify_target("test ::ffff:192.168.1.1 box")
    assert cls is TargetClass.SAFE


def test_full_form_public_ipv6_is_real():
    cls, _ = classify_target("target 2600:1234:5678:9abc:def0:1234:5678:9abc")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


# ── Substring-marker bypass regression (review G3-1): real public domains that merely
#    CONTAIN a safe marker as a substring must NOT be classified SAFE. ──

def test_real_domain_containing_ctf_substring_is_not_safe():
    cls, target = classify_target("full compromise of ctfake-corp.com")
    assert cls is TargetClass.REAL_PUBLIC_INFRA
    assert target == "ctfake-corp.com"


def test_real_domain_containing_htb_substring_is_not_safe():
    cls, _ = classify_target("rce on htbank.com")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_real_domain_containing_thm_substring_is_not_safe():
    cls, _ = classify_target("attack thmart-store.com")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_real_domain_with_example_com_as_subdomain_prefix_is_not_safe():
    # myexample.com.attacker.net contains "example.com" but is attacker-controlled.
    cls, _ = classify_target("attack myexample.com.attacker.net")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_real_domain_containing_localhost_substring_is_not_safe():
    cls, _ = classify_target("attack victim-localhost-bank.com")
    assert cls is TargetClass.REAL_PUBLIC_INFRA


def test_genuine_lab_suffix_still_safe():
    # Legitimate safe targets must still pass after the tightening.
    assert classify_target("exploit https://target.lab.example.com/api")[0] is TargetClass.SAFE
    assert classify_target("test app.internal/login")[0] is TargetClass.SAFE
    assert classify_target("scan box.htb")[0] is TargetClass.SAFE  # HackTheBox .htb TLD
    assert classify_target("rce on example.com")[0] is TargetClass.SAFE


# ───────────────────────── requires_authorization (pure matrix) ─────────────────────────


def test_requires_auth_real_public_offensive_unauthorized_blocks():
    assert requires_authorization(
        TargetClass.REAL_PUBLIC_INFRA, is_offensive=True, authorized=False
    ) is True


def test_requires_auth_cleared_when_authorized():
    assert requires_authorization(
        TargetClass.REAL_PUBLIC_INFRA, is_offensive=True, authorized=True
    ) is False


def test_requires_auth_safe_target_never_blocks():
    assert requires_authorization(
        TargetClass.SAFE, is_offensive=True, authorized=False
    ) is False


def test_requires_auth_non_offensive_never_blocks():
    # OSINT / intelligence (passive) against public infra is not a live attack.
    assert requires_authorization(
        TargetClass.REAL_PUBLIC_INFRA, is_offensive=False, authorized=False
    ) is False


def test_requires_auth_no_target_never_blocks():
    assert requires_authorization(
        TargetClass.NONE, is_offensive=True, authorized=False
    ) is False


# ───────────────────────── evaluate_authorization (end-to-end decision) ─────────────────────────


def test_evaluate_blocks_real_attack_without_optin():
    d = evaluate_authorization(
        message="leo thang đặc quyền + kiểm soát toàn bộ hệ thống qua ip 117.5.141.7",
        is_offensive=True,
        authorized=False,
    )
    assert isinstance(d, AuthorizationDecision)
    assert d.blocked is True
    assert d.target == "117.5.141.7"
    assert d.target_class is TargetClass.REAL_PUBLIC_INFRA
    assert d.reason


def test_evaluate_allows_with_optin():
    d = evaluate_authorization(
        message="pentest 117.5.141.7", is_offensive=True, authorized=True
    )
    assert d.blocked is False


def test_evaluate_allows_lab_target():
    d = evaluate_authorization(
        message="pentest target.lab.example.com", is_offensive=True, authorized=False
    )
    assert d.blocked is False


def test_evaluate_allows_non_security_task():
    d = evaluate_authorization(
        message="Design a biomass gasifier", is_offensive=False, authorized=False
    )
    assert d.blocked is False


# ── Independent offensive-intent backstop (B1): the gate must not depend solely on
#    the LLM's task_type, since that classifier can mislabel a real attack. ──

def test_detect_offensive_explicit_english():
    assert detect_offensive_intent("achieve remote code execution on the server") is True
    assert detect_offensive_intent("privilege escalation to root and full control") is True
    assert detect_offensive_intent("exploit the vulnerability to gain a shell") is True


# Each phrase must trigger ON ITS OWN — guards against the verification-theater bug
# where a test "covered" a phrase only because another phrase in the string matched.
@pytest.mark.parametrize("phrase", [
    "achieve remote code execution",
    "exploit the vulnerability",          # was a FALSE pass before (matched "gain a shell")
    "escalate privileges to root",        # plural — was missed
    "get root on the box",
    "obtain a shell on the host",
    "drop a webshell",
    "dump the database",
    "exfiltrate the customer data",
    "install a backdoor for persistence",
    "lateral movement to the domain controller",
    "sql injection on the login form",
    "command injection in the upload handler",
    "brute force the ssh login",
    "pass the hash to the file server",
    "deface the homepage",
    "deploy ransomware across the network",
    # Vietnamese
    "chiếm shell trên máy chủ",
    "đoạt quyền root",
    "cài backdoor vào hệ thống",
    "đánh cắp dữ liệu khách hàng",
    "dò mật khẩu đăng nhập",
])
def test_detect_offensive_each_phrase_triggers_alone(phrase):
    assert detect_offensive_intent(phrase) is True


def test_detect_offensive_vietnamese():
    assert detect_offensive_intent(
        "leo thang đặc quyền và kiểm soát toàn bộ hệ thống qua target ip 117.5.141.7"
    ) is True
    assert detect_offensive_intent("khai thác lỗ hổng để chiếm quyền điều khiển") is True


def test_detect_offensive_false_on_osint_and_defensive():
    # Passive / defensive / benign work must NOT trip the backstop.
    assert detect_offensive_intent("OSINT investigation of acme-corp.com") is False
    assert detect_offensive_intent("monitor the network and audit the firewall config") is False
    assert detect_offensive_intent("write a unit test for the login attack surface doc") is False
    assert detect_offensive_intent("Design a 20 kWe biomass gasifier generator") is False


def test_detect_offensive_false_on_bare_ambiguous_words():
    # Single ambiguous words ("test", "attack") must not fire on their own.
    assert detect_offensive_intent("run the test suite") is False
    assert detect_offensive_intent("analyze the attack surface and report findings") is False


def test_detect_offensive_false_on_business_phrasing():
    # "take over"/"compromise"/"control" in non-infra senses must NOT fire — these
    # phrases are anchored to infrastructure nouns to stay narrow.
    assert detect_offensive_intent("take over the documentation from the old team") is False
    assert detect_offensive_intent("a good compromise the stakeholders can accept") is False
    assert detect_offensive_intent("give the user full control of the dashboard layout") is False


def test_detect_offensive_substring_boundary_guard():
    # Word boundaries: "rce on" must not match inside "enforce only"/"reinforce on".
    assert detect_offensive_intent("enforce only the rate limit on the API") is False
    assert detect_offensive_intent("reinforce on the backend layer") is False


def test_detect_offensive_true_on_infra_anchored_phrases():
    assert detect_offensive_intent("compromise the server and pivot internally") is True
    assert detect_offensive_intent("take over the host to prove impact") is True


# ───────────────────────── env opt-in reader ─────────────────────────


def test_env_optin_default_false(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    assert is_live_offensive_authorized() is False


def test_env_optin_truthy(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_OFFENSIVE", "1")
    assert is_live_offensive_authorized() is True
    monkeypatch.setenv("ALLOW_LIVE_OFFENSIVE", "true")
    assert is_live_offensive_authorized() is True
    monkeypatch.setenv("ALLOW_LIVE_OFFENSIVE", "no")
    assert is_live_offensive_authorized() is False


# ───────────────────────── pipeline._check_authorization (integration) ─────────────────────────


def _pipeline():
    return NexusExecutionPipeline(llm=None, tool_executor=None, task_id="t-c2")


@pytest.mark.asyncio
async def test_pipeline_blocks_live_attack_on_public_ip(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "leo thang đặc quyền + kiểm soát toàn bộ hệ thống qua ip 117.5.141.7",
        task_type="security",
    )
    assert result is not None
    assert result["metadata"]["status"] == "blocked_authorization"
    assert "117.5.141.7" in result["content"]
    # The block must NOT leak any credential the user may have included.
    assert "no agents were spawned" in result["content"].lower()


@pytest.mark.asyncio
async def test_pipeline_allows_when_opted_in(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_OFFENSIVE", "1")
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "pentest 117.5.141.7", task_type="security"
    )
    assert result is None


@pytest.mark.asyncio
async def test_pipeline_allows_lab_target(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "pentest target.lab.example.com", task_type="security"
    )
    assert result is None


@pytest.mark.asyncio
async def test_pipeline_does_not_gate_intelligence_task(monkeypatch):
    # Passive OSINT/intel against public infra is not a live attack → never gated.
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "OSINT investigation of acme-corp-prod.net", task_type="intelligence"
    )
    assert result is None


@pytest.mark.asyncio
async def test_pipeline_does_not_gate_general_task(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "Design a 20 kWe biomass gasifier", task_type="general"
    )
    assert result is None


# ── Resume path must NOT bypass the gate (review G3-4). task_type is persisted on
#    ExecutionState and restored from the checkpoint, so the gate re-runs on resume. ──

@pytest.mark.asyncio
async def test_pipeline_gates_attack_misclassified_as_general(monkeypatch):
    # The motivating failure: LLM labels a real attack "general" → the gate must
    # STILL fire via the independent offensive-intent backstop.
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "leo thang đặc quyền + kiểm soát toàn bộ hệ thống qua ip 117.5.141.7",
        task_type="general",
    )
    assert result is not None
    assert result["metadata"]["status"] == "blocked_authorization"


@pytest.mark.asyncio
async def test_pipeline_backstop_does_not_gate_osint_on_public(monkeypatch):
    # OSINT/intel on public infra is passive → backstop must not fire, gate passes.
    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    pipe = _pipeline()
    result = await pipe._check_authorization(
        "OSINT investigation of acme-corp-prod.net", task_type="general"
    )
    assert result is None


def test_execution_state_carries_task_type():
    from app.orchestration.models import ExecutionState

    st = ExecutionState(task_id="t", task_type="security")
    assert st.task_type == "security"


def test_checkpoint_serializes_and_restores_task_type():
    from app.core.checkpoint_persistence import CheckpointPersistence
    from app.orchestration.models import ExecutionState

    st = ExecutionState(task_id="t", user_message="m", task_type="security")
    blob = CheckpointPersistence._serialize_state(st)
    assert blob["task_type"] == "security"
    # Restore path reads state_json.get("task_type", "general").
    pipe = _pipeline()
    restored = pipe._restore_or_create_state("m", {"state_json": blob, "turn_number": 3})
    assert restored.task_type == "security"
