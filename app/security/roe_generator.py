"""
MekongAI Nexus - ROE (Rules of Engagement) Generator for Security Operations.

LLM-driven classification and content generation. No keyword matching —
the LLM understands intent/language/context far better than regex ever could.

Regex is ONLY used for target extraction (domain/IP/CIDR are fixed formats).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Target extraction (regex is appropriate here — domains/IPs have fixed syntax)
# ---------------------------------------------------------------------------

_DOMAIN_RE = re.compile(
    r"(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z0-9][-a-zA-Z0-9]*)+)"
)
_ANDROID_PACKAGE_RE = re.compile(r"(?:id=|package[:\s=])([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*){2,})")
_APPSTORE_PLATFORMS = {"play.google.com", "apps.apple.com", "appstore.com", "itunes.apple.com"}
_IP_RE = re.compile(
    r"\b((?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?))\b"
)
_CIDR_RE = re.compile(
    r"\b((?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)/\d{1,2})\b"
)
_WALLET_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[A-Za-z1-9]{33})\b")
_CVE_RE = re.compile(r"\b(CVE-\d{4}-\d{4,7})\b", re.IGNORECASE)


def _extract_target(task_description: str) -> str:
    """Extract target identifier from request (regex — format-based, not semantic)."""
    if not task_description:
        return "(to be declared)"

    pkg_match = _ANDROID_PACKAGE_RE.search(task_description)
    if pkg_match:
        return pkg_match.group(1)

    domains = _DOMAIN_RE.findall(task_description)
    real_domains = [d for d in domains if d not in _APPSTORE_PLATFORMS]
    if real_domains:
        return real_domains[0]
    if domains:
        return domains[0]

    m = _CIDR_RE.search(task_description)
    if m:
        return m.group(1)

    m = _IP_RE.search(task_description)
    if m:
        return m.group(1)

    m = _WALLET_RE.search(task_description)
    if m:
        return m.group(1)

    m = _CVE_RE.search(task_description)
    if m:
        return m.group(1)

    # M3 (audit 2026-06-29): no structured identifier found. Do NOT dump raw user
    # prose into the ROE target field (a jailbreak sentence would land verbatim in
    # the machine-readable Subject/Primary Target). Return a safe placeholder; the
    # authorization gate classifies an undeclared target as NONE anyway.
    return "(to be declared)"


# ---------------------------------------------------------------------------
# LLM-based classification
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """You are a security engagement classifier. Given a task description,
determine the engagement parameters. Respond with ONLY valid JSON:

{
  "roe_type": "pentest" | "intel" | "none",
  "engagement_type": "<specific type, e.g. 'Red Team (Full Scope)', 'Web Application / API', 'Internal Network', 'OSINT Collection', etc.>",
  "engagement_goal": "<specific objective, e.g. 'Remote Code Execution (RCE) — achieve command execution on target server'>",
  "operation_type": "<for intel: OSINT, Forensics, Threat Hunting, FININT, etc. Empty for pentest.>"
}

Rules:
- "pentest" = any offensive security: red team, penetration test, exploit, vulnerability assessment, security audit, attack simulation
- "intel" = any investigation/intelligence: OSINT, forensics, threat hunting, incident response, compliance audit, surveillance, tracing
- "none" = not a security task
- engagement_goal should be specific: what does the requester want to ACHIEVE? (RCE, data exfil, privesc, full compromise, credential access, etc.)
- engagement_type should match the attack surface (web app, network, cloud, mobile, wireless, red team full scope)
- Understand ANY language — Vietnamese, English, mixed, slang, technical jargon"""


async def _classify_task(task_description: str, llm: Any) -> dict[str, str]:
    """Use LLM to classify whether task needs security ROE and extract parameters."""
    messages = [
        {"role": "system", "content": _CLASSIFY_PROMPT},
        {"role": "user", "content": task_description},
    ]
    try:
        result = await llm.chat(messages=messages, temperature=0.0, max_tokens=512)
        content = result.get("content", "")
        # Extract JSON from response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)
    except Exception as e:
        logger.warning("roe_classify_failed", error=str(e))
        return {"roe_type": "none", "engagement_type": "", "engagement_goal": "", "operation_type": ""}


# ---------------------------------------------------------------------------
# ROE Templates
# ---------------------------------------------------------------------------

_PENTEST_ROE_TEMPLATE = """# Rules of Engagement — Security Assessment

## Authorization Statement
{authorization_statement}

## Target Scope
- **Primary Target**: {target}
- **In scope**: The primary target and the infrastructure it directly owns/operates
  (its own backend APIs, auth servers, and databases).
- **Out of scope unless the owner explicitly declared them**: third-party services the
  target merely integrates with, shared CDNs, and distribution channels operated by
  others (app stores, mirrors). Testing these without explicit authorization exceeds scope.
- **Engagement Type**: {engagement_type}

## Hard Constraints (Cannot Be Overridden)
- No denial of service (DoS) or volumetric testing
- No data destruction or permanent modification on target
- No persistence / backdoors that survive engagement closure
- No credential stuffing or brute force outside explicit scope
- No mass scanning of public internet outside declared scope
- No self-propagating implants or worms
- No false-flag operations impersonating real third parties
- No exploitation of safety-of-life systems without safety review
- Minimal evidence only — prove access/impact without dumping large datasets

## OPSEC Classification
Tag EVERY action with noise level:
- **QUIET**: Passive, unlikely to trigger alerts (DNS, WHOIS, cert transparency)
- **MODERATE**: Active but common traffic (TCP connect scans, HTTP requests)
- **LOUD**: Likely to trigger IDS/IPS/WAF/SOC alerts (vuln scans, brute force)

When a quieter alternative exists, prefer it. Escalate noise only when necessary.

---

## Operational Methodology — Phase-by-Phase Playbook

> This playbook is the **general methodology reference** (the standard recon →
> analysis → exploitation → post-exploitation → reporting flow). The **specific
> phases, their order, and the per-phase deliverables for THIS task** are defined
> in TASK_BRIEF.md / TODO.md and take precedence — follow the task's phase plan
> and map each task phase onto the techniques below. The phase numbering here is
> illustrative, not a fixed count.

### Phase 1: Reconnaissance (QUIET → MODERATE)

**Objective:** Build complete target profile from zero knowledge.

**Passive Recon (QUIET) — do FIRST:**
- WHOIS lookup, DNS records (A, AAAA, MX, NS, TXT, CNAME, SOA)
- Certificate transparency logs (crt.sh) — discover subdomains
- Historical DNS (SecurityTrails, DNSdumpster patterns)
- Web archive (Wayback Machine) — find old endpoints, leaked paths
- Google dorks: `site:{{target}}`, `inurl:admin`, `filetype:sql|env|bak|conf`
- Technology fingerprinting: Wappalyzer-style HTTP header/response analysis
- Shodan/Censys for exposed services and historical banners
- GitHub/GitLab search for leaked credentials, configs, source code referencing target
- Social engineering recon: LinkedIn (staff), job postings (tech stack hints)

**Active Recon (MODERATE) — after passive is exhausted:**
- HTTP crawling: sitemap.xml, robots.txt, common paths (/admin, /api, /.env, /wp-json)
- Port scan: top 1000 TCP ports, then full 65535 if initial scan reveals non-standard services
- Service version detection on open ports
- Virtual host enumeration (Host header fuzzing)
- Web application mapping: endpoints, parameters, authentication flows
- API discovery: OpenAPI/Swagger, GraphQL introspection, WADL

**Phase 1 Deliverables (MUST complete before advancing):**
- [ ] IP addresses and hosting provider identified
- [ ] Full subdomain list enumerated
- [ ] Technology stack identified (web server, framework, CMS, WAF, CDN)
- [ ] Open ports and services mapped
- [ ] Authentication mechanisms documented
- [ ] Attack surface ranked by likelihood of exploitation

**Transition Rule:** Advance to Phase 2 ONLY when you have enough surface knowledge to
identify at least 3 potential attack vectors. If recon yields nothing, widen scope (more
subdomains, related infrastructure) before giving up.

### Phase 2: Vulnerability Analysis (MODERATE)

**Objective:** Identify exploitable weaknesses from the mapped attack surface.

**Web Application Testing (OWASP Top 10 + beyond):**
- Injection points: SQL injection (error-based, blind, time-based), NoSQL injection,
  LDAP injection, OS command injection, SSTI (template injection)
- Authentication flaws: default credentials, weak password policy, session fixation,
  JWT manipulation, OAuth misconfigurations
- Access control: IDOR, privilege escalation paths, forced browsing, missing
  function-level access control
- File upload/include: unrestricted upload, LFI/RFI, path traversal
- Deserialization: Java/PHP/Python/Ruby unsafe deserialization
- SSRF: internal service access via URL parameters
- XSS (stored/reflected/DOM) — useful as pivot for session hijacking
- XXE: XML external entity injection for file read/SSRF

**Infrastructure Testing:**
- Known CVEs for detected service versions (search exploit-db, NVD)
- Default/weak configurations: directory listing, debug modes, verbose errors
- SSL/TLS weaknesses: outdated protocols, weak ciphers, certificate issues
- Cloud misconfigurations: open S3 buckets, exposed metadata endpoints (169.254.169.254)

**Methodology:**
- For each potential vulnerability: test with minimal payloads first (fingerprint)
- Confirm existence before attempting full exploitation
- Cross-verify: if SQLi is suspected, try multiple techniques to confirm
- Prioritize by RCE likelihood: command injection > deserialization > SQLi > file upload > SSRF

**Phase 2 Deliverables:**
- [ ] Vulnerability list with confidence grades (Confirmed/Probable/Possible)
- [ ] Each vuln mapped to potential impact (RCE, data access, lateral movement)
- [ ] Attack chain candidates ranked by: stealth x reliability x impact
- [ ] WAF/IDS bypass strategies identified if protections detected

**Transition Rule:** Advance to Phase 3 when at least 1 vulnerability is CONFIRMED
exploitable with a clear path to the engagement objective. If no confirmed vulns after
thorough analysis, document findings and reassess scope with task owner.

### Phase 3: Exploitation (MODERATE → LOUD if needed)

**Objective:** Achieve the engagement goal ({engagement_goal}) through proven attack chain.

**Exploitation Principles:**
- Start with highest-confidence, lowest-noise vector
- Build exploit incrementally: PoC → reliable trigger → payload delivery → execution
- If first vector fails, fall back to next-ranked candidate — don't fixate
- Test in stages: 1) confirm vuln fires, 2) confirm output control, 3) achieve objective
- For RCE goals: command execution → reverse shell → stable access

**Common Exploitation Patterns:**
- Web shell upload via file upload bypass (extension, content-type, magic bytes)
- SQL injection → stacked queries → xp_cmdshell / LOAD_FILE / INTO OUTFILE
- Command injection via unsanitized parameters
- Deserialization RCE via crafted gadget chains
- SSTI → code execution (Jinja2: {{{{config.__class__.__init__.__globals__}}}}, etc.)
- SSRF → cloud metadata → credentials → lateral movement
- Known CVE exploits for detected software versions (adapt public PoC)

**Stability Requirements:**
- Exploit must be reproducible (not a one-time race condition fluke)
- If shell access achieved: verify stability (can survive for documentation)
- Avoid exploits that crash the service (violates no-DoS constraint)

**Phase 3 Deliverables:**
- [ ] Working exploit with exact reproduction steps
- [ ] Proof of impact (command output, file read, shell access — depends on goal)
- [ ] Full request/response evidence chain
- [ ] Stealth assessment: was exploitation likely detected?

**Transition Rule:** If engagement goal is achieved, proceed to Phase 4 (Post-Exploitation)
for impact demonstration. If goal cannot be achieved with available vectors, document
maximum achieved access and report as-is.

### Phase 4: Post-Exploitation (if access achieved)

**Objective:** Demonstrate full impact of the compromise.

**Immediate Actions (upon gaining access):**
- Identify: whoami, hostname, OS version, architecture
- Network position: ifconfig/ip addr, internal subnets, routing table
- Enumerate: local users, running services, installed software, cron jobs
- Credentials: config files, environment variables, database connection strings,
  SSH keys, browser credential stores, password files
- Lateral movement potential: internal services accessible, other hosts visible

**Evidence Collection:**
- System info: OS, kernel, architecture, hostname
- Network: internal IPs, listening ports, ARP table, DNS config
- Users: local accounts, logged-in sessions, sudo permissions
- Interesting files: /etc/passwd, /etc/shadow (if readable), .bash_history,
  web app configs (database creds), .env files, SSH keys
- Running processes and services

**Phase 4 Deliverables:**
- [ ] System profile (OS, users, network, services)
- [ ] Credentials or secrets found
- [ ] Lateral movement paths identified
- [ ] Impact statement: what an attacker could do from this position

### Phase 5: Reporting

**Objective:** Professional report documenting the full engagement.

Each finding MUST include:
1. Severity (Critical/High/Medium/Low/Info) + CVSS v3.1 score
2. ATT&CK technique ID
3. Affected systems (IP/hostname/URL)
4. Evidence (exact requests/responses)
5. Reproduction steps (copy-pasteable commands)
6. Business impact assessment
7. Remediation recommendation (specific, not generic)
8. Verification method (how to confirm the fix)

---

## Decision Logic & Escalation

**When stuck (no progress after reasonable effort):**
1. Widen reconnaissance — there are ALWAYS more subdomains, endpoints, parameters
2. Try alternative techniques for the same vuln class (different SQLi types, different payloads)
3. Pivot to a different attack vector entirely
4. Check for recently disclosed CVEs matching detected versions
5. If truly blocked: document maximum achieved access, clearly state what was attempted

**When WAF/protection blocks exploitation:**
- Identify WAF vendor (response headers, error pages)
- Try encoding bypasses: URL encoding, double encoding, unicode, case variation
- Try protocol-level bypasses: HTTP/2 downgrade, chunked transfer, parameter pollution
- Try payload alternatives: different functions, different syntax for same result
- If WAF is impenetrable: report it as a defensive strength and try other vectors

**NEVER give up silently.** If a phase yields zero results, explicitly document:
what was tried, why it failed, and what would need to change for it to succeed.

---

## Evidence & Audit Trail (MANDATORY)
- **Every action MUST be logged** via `audit_log` tool — no exceptions
- Record: action type, target, parameters, output/evidence, severity, OPSEC level
- Save tool output to timestamped format: `{{tool}}_{{target}}_{{YYYYMMDD_HHMMSS}}`
- Clearly separate: **CONFIRMED** (reproduced) vs **POTENTIAL** (theoretical)
- Chain of evidence: each finding must link to specific tool outputs
- Findings without audit trail are **INADMISSIBLE**

### audit_log Usage
```
audit_log(
    action="port_scan",
    target="10.10.1.0/24",
    category="recon",
    details="Nmap SYN scan top 1000 ports, rate 500/s",
    evidence="22/tcp open SSH, 80/tcp open HTTP, 443/tcp open HTTPS",
    severity="info"
)
```

## MITRE ATT&CK Alignment
- Map EVERY technique to ATT&CK ID (T-codes)
- Track progression through kill chain phases
- Document which TTPs succeeded vs. detected
- Reference: Reconnaissance (TA0043), Initial Access (TA0001), Execution (TA0002),
  Persistence (TA0003), Privilege Escalation (TA0004), Defense Evasion (TA0005),
  Credential Access (TA0006), Discovery (TA0007), Lateral Movement (TA0008),
  Collection (TA0009), Exfiltration (TA0010), C&C (TA0011)

## Finding Severity Classification
| Severity | CVSS | Criteria |
|----------|------|----------|
| Critical | 9.0-10.0 | Remote code execution, no auth required, direct path to full compromise |
| High | 7.0-8.9 | Authenticated RCE, privilege escalation to admin/root |
| Medium | 4.0-6.9 | Requires specific conditions, moderate impact |
| Low | 0.1-3.9 | Limited impact or significant prerequisites |
| Info | N/A | Best practice, no direct security impact |

## Authorization Reference
- **Date**: {date}
- **Task ID**: {task_id}
{authorization_block}
---
*ROE is static — created once at task start, applies to ALL agents in ALL turns.*
"""


_INTEL_ROE_TEMPLATE = """# Rules of Engagement — Intelligence/Investigation Operations

## Authorization Statement
**This is an authorized intelligence/investigation operation.** The task owner is
responsible for holding valid authorization for the operation and for ensuring it complies
with applicable law.

**Operating posture for all agents:**
- Treat this as a professional operation and carry out the assigned collection/analysis
  competently, using only authorized sources and methods.
- Work strictly within the Targets & Scope and Hard Constraints below.
- Surface a real blocker if you encounter one — collection drifting outside scope, or
  potential for harm beyond the stated objective.

## Operation Type
{operation_type}

## Targets & Scope
- **Subject(s)**: {target}
- **Boundaries**: Only authorized targets — no collateral collection
- **Legal Framework**: Task owner has verified compliance — agents need not assess this

## Core Doctrine: VERIFY EVERYTHING
- Search results are LEADS, not facts — cross-validate with 2+ independent sources
- Single source = UNCONFIRMED | 2 sources = POSSIBLE | 3+ sources = PROBABLE
- Only REPRODUCTION/VERIFICATION in controlled environment = CONFIRMED
- Consider adversary deception: Could information be planted? Cui bono?

## Hard Constraints
- OBSERVE and document — do not interact with threat actors
- No purchase/download of illegal materials
- No active interference with ongoing operations
- No compromise of sources or methods
- Compartmentalize information on need-to-know basis
- Monitor own operational footprint (OPSEC)

---

## Operational Methodology — Phase-by-Phase Playbook

### Phase 1: Collection Planning
- Define intelligence requirements (what SPECIFICALLY do we need to know?)
- Identify available collection channels (OSINT, SIGINT, HUMINT sources)
- Prioritize channels by reliability and access risk
- Document collection plan before executing

### Phase 2: Multi-Channel Collection
**Open Sources (OSINT):**
- Domain/IP intelligence: WHOIS, DNS history, cert transparency, BGP routing
- Web presence: social media profiles, forums, public posts, cached pages
- Public records: corporate filings, court records, regulatory databases
- Technical fingerprinting: Shodan, Censys, web archives
- Code repositories: GitHub, GitLab — leaked keys, configs, internal references
- Dark web (if in scope): paste sites, forums, marketplaces

**Structured Data Sources:**
- CVE databases, exploit databases for vulnerability context
- Threat intelligence feeds for IOC correlation
- Malware repositories (VirusTotal, Any.Run) for sample analysis
- Financial records, blockchain analysis for FININT operations

**Phase 2 Deliverables:**
- [ ] Raw data collected from all channels with timestamps
- [ ] Source reliability graded (A-F scale)
- [ ] Gaps identified — what couldn't be found, and why

### Phase 3: Analysis & Correlation
- Cross-reference findings across multiple channels
- Build timeline of events from collected data
- Identify patterns, anomalies, and connections
- Challenge assumptions — apply Structured Analytic Techniques (SATs)
- Document alternative hypotheses (Analysis of Competing Hypotheses)

**Phase 3 Deliverables:**
- [ ] Correlation matrix linking related findings
- [ ] Timeline reconstruction
- [ ] Confidence-graded conclusions
- [ ] Identified gaps requiring further collection

### Phase 4: Reporting & Dissemination
- Executive summary with key findings and confidence levels
- Detailed technical appendix with evidence chain
- Recommendations for action based on intelligence
- Identified limitations and suggested follow-up collection

---

## Decision Logic & Escalation

**When a source contradicts existing findings:**
1. Do NOT discard either — document the contradiction
2. Seek independent third source to arbitrate
3. Consider which source has stronger provenance
4. If unresolvable: report both with respective confidence grades

**When collection yields insufficient data:**
1. Expand to additional channels not yet tried
2. Try alternative search terms, languages, or time ranges
3. Consider indirect indicators (metadata, relationships, patterns)
4. If truly exhausted: explicitly report "Insufficient data" — never fabricate

---

## Audit Trail (MANDATORY)
- **Every action MUST be logged** via `audit_log` tool
- Record: action, target, methodology, findings, confidence grade
- Evidence preservation: hash (SHA-256) + timestamp everything
- Grade ALL intelligence:
  - **A1** (Reliable source, confirmed) → **CONFIRMED**
  - **B2** (Usually reliable, probably true) → **PROBABLE**
  - **C3** (Fairly reliable, possibly true) → **POSSIBLE**
  - **D4-F6** (Unreliable/unknown) → **UNCONFIRMED**

### audit_log Usage
```
audit_log(
    action="osint_domain_profile",
    target="suspect-domain.com",
    category="osint",
    details="WHOIS lookup + DNS history + cert transparency",
    evidence="Registered 2025-01-15, registrant: privacy-protected, NS: cloudflare",
    severity="info"
)
```

## Reporting Standard
- State what evidence SHOWS, not what you THINK happened
- Confidence levels: Confirmed / Highly likely / Probable / Possible / Insufficient
- Every claim must reference specific artifacts
- Acknowledge limitations and gaps in evidence
- Never overstate certainty

## Authorization Reference
- **Date**: {date}
- **Task ID**: {task_id}
{authorization_block}
---
*ROE is static — created once at task start, applies to ALL agents in ALL turns.*
"""


# ---------------------------------------------------------------------------
# Generator (public API)
# ---------------------------------------------------------------------------


# Planner deliverable modes whose product is knowledge or an own-infra artifact,
# NOT an offensive engagement. When the planner declares one of these, the ROE
# generator must not render the attack playbook even if its own classifier
# disagrees (CC-10/CC-12: the two layers were divorced and the offensive branch
# silently won). MIXED / empty leave the offensive engagement path open.
_NON_OFFENSIVE_CONTRACT_MODES = frozenset({"RESEARCH", "ANALYZE", "BUILD"})


async def generate_roe(
    task_description: str,
    task_id: str,
    llm: Optional[Any] = None,
    roe_type: str = "",
    engagement_type: str = "",
    engagement_goal: str = "",
    operation_type: str = "",
    task_type: str = "security",
    contract_mode: str = "",
) -> str:
    """Generate ROE.md content.

    If llm is provided: uses LLM classification to determine ROE type and parameters.
    If llm is None but roe_type is provided: uses the explicit parameters (from orchestrator).
    Fallback: when classification is inconclusive, render the passive intel ROE
    (never auto-arm with the pentest playbook).

    ``contract_mode`` is the planner's deliverable-contract mode
    (BUILD/RESEARCH/ANALYZE/MIXED). It is the AUTHORITATIVE intent signal: when it
    names a non-offensive deliverable, the offensive pentest playbook is suppressed
    regardless of the generator's own classifier (fail-closed — the safe branch
    wins on disagreement between the two layers).
    """
    target = _extract_target(task_description)

    # Strategy 1: LLM classification (best accuracy)
    if llm and not roe_type:
        classification = await _classify_task(task_description, llm)
        roe_type = classification.get("roe_type", "none")
        engagement_type = classification.get("engagement_type", "") or engagement_type
        engagement_goal = classification.get("engagement_goal", "") or engagement_goal
        operation_type = classification.get("operation_type", "") or operation_type

    # Strategy 2: Explicit parameters from orchestrator (already LLM-classified)
    # roe_type passed directly — no keyword matching needed

    # FAIL-CLOSED INTENT CHECK: if the planner scoped this as a non-offensive
    # deliverable (research/analyze/build), downgrade an offensive "pentest"
    # classification to the passive intel ROE. The two layers must agree; on
    # disagreement the safe branch wins.
    if roe_type == "pentest" and contract_mode.strip().upper() in _NON_OFFENSIVE_CONTRACT_MODES:
        logger.info(
            "roe_intent_downgrade",
            reason="planner_contract_non_offensive",
            contract_mode=contract_mode.upper(),
            task_id=(task_id[:8] if task_id else ""),
        )
        roe_type = "intel"

    # Generate appropriate ROE
    if roe_type == "pentest":
        return _render_pentest_roe(target, task_id, engagement_type, engagement_goal)
    elif roe_type == "intel":
        return _render_intel_roe(target, task_id, operation_type)
    else:
        # Inconclusive classification ("none"/empty): do NOT auto-arm. Rendering
        # the offensive pentest playbook (recon→vuln→exploitation→post-exploitation)
        # for a task the classifier could not confirm is offensive is exactly the
        # auto-arm path the audit flagged (CC-10/CC-12). Fail safe: render the
        # passive intel/investigation ROE (observe & document, no exploitation
        # phases). An explicitly-classified pentest above is unaffected.
        return _render_intel_roe(target, task_id, operation_type)


def _authorization_posture(target: str = "") -> str:
    """Classify the authorization posture for ``target`` — computed ONCE so the
    ROE header and footer never contradict each other (task 3c44a93f bug #5b).

    Returns one of:
      - "in_scope":     target IS declared in pentest_scope.yaml (a partner
                        testing assets covered by a documented engagement). The
                        engagement is authorized; the full assessment proceeds.
      - "needs_proof":  target is real public-routable infra NOT in scope —
                        per-target ownership/permission proof is required first.
      - "self_attested": neither of the above (e.g. lab/owned host); the task
                        owner attests authorization.
    """
    if target:
        try:
            from app.security.pentest_scope import matching_entry
            if matching_entry(target):
                return "in_scope"
        except Exception:
            pass
        try:
            from app.security.authorization_gate import TargetClass, classify_target
            tclass, _ = classify_target(target)
            if tclass is TargetClass.REAL_PUBLIC_INFRA:
                return "needs_proof"
        except Exception:
            pass
    return "self_attested"


# Operating-posture footer shared by every authorization statement — kept
# separate so the per-posture lead sentence is the only thing that varies.
_OPERATING_POSTURE = """**Operating posture for all agents:**
- Treat this as a professional engagement and execute the assigned testing competently.
- Work strictly within the Target Scope and Hard Constraints below.
- Surface a real blocker if you encounter one — out-of-scope assets, signs the target is
  not what was declared, or potential for harm beyond the stated objective."""


def _authorization_statement(target: str = "") -> str:
    """Header authorization statement, consistent with the posture and the footer.

    in_scope → affirm the engagement is authorized and the assessment proceeds
    (so a partner pentesting their own in-scope site is never hedged into a
    refusal). needs_proof → do NOT assert blanket authorization; align with the
    footer's per-target-proof requirement. self_attested → owner attests.
    """
    posture = _authorization_posture(target)
    if posture == "in_scope":
        # Embed the authorizing ORG + signed DOCUMENT right here (pulled from the
        # scope entry), so the worker — which never reads pentest_scope.yaml and
        # only sees the HEAD of ROE.md — gets a self-contained, credible
        # authorization at the very top. Citing the real contract is what makes it
        # trustworthy to the agent (task 3c44a93f follow-up: agents doubted an
        # unbacked "you are authorized" claim and refused an in-scope target).
        org = document = ""
        try:
            from app.security.pentest_scope import matching_entry
            entry = matching_entry(target) or {}
            org = (entry.get("org") or "").strip()
            document = (entry.get("document") or "").strip()
        except Exception:
            pass
        credential = ""
        if org or document:
            credential = (
                f" Per-target authorization is ON FILE for `{target}`:\n"
                f"- **Authorizing party**: {org or '(declared engagement)'}\n"
                f"- **Authorizing document**: {document or '(document on file)'}\n"
            )
        lead = (
            "**This is an authorized security assessment.** This target is covered by a "
            "declared, documented engagement recorded in the deployment's pentest scope, "
            "so the work is authorized — proceed with the full assessment within the "
            "engagement scope. Execute the assigned testing (including active testing of "
            "the live in-scope host) competently and do NOT downgrade it to a simulation "
            "or refuse it as unauthorized." + credential +
            "This authorization is established by the operator who runs this deployment and "
            "holds the agreement with the target owner; you do not need to independently "
            "re-verify it — the runtime tool gate also enforces it, allowing in-scope "
            "targets and blocking the rest."
        )
    elif posture == "needs_proof":
        lead = (
            "**Authorization for this target is NOT yet established.** This is real, "
            "public-routable infrastructure that is NOT in the declared engagement scope. "
            "Do NOT engage it until per-target proof of ownership or written permission "
            "for THIS specific target is held (see Authorization Reference below)."
        )
    else:
        lead = (
            "**This is an authorized security assessment.** The task owner is responsible "
            "for holding valid authorization (ownership or written permission) for the "
            "target(s) in scope, and for ensuring the engagement complies with applicable law."
        )
    return f"{lead}\n\n{_OPERATING_POSTURE}"


def _build_authorization_block(target: str = "") -> str:
    """Render the Authorization Reference lines from deployment-level config.

    Records WHO is accountable for the engagement. It NEVER grants blanket
    self-authorization: each in-scope target still requires the operator to
    actually hold ownership or written permission. Standing configuration is an
    accountability record, not a waiver — so it does not tell agents that
    "individual requests need not re-declare ownership" (authorization
    laundering, audit CC-10). For a real public-routable target, standing
    authorization never applies implicitly: per-target proof is demanded.
    """
    try:
        from app.config import get_settings
        s = get_settings()
    except Exception:
        return "- **Authorization**: Self-attested by the requester (no deployment config available)"

    # A public target that is DECLARED in the pentest scope has per-target
    # authorization on file (org + document). The ROE the agent reads must say so
    # — otherwise the agent believes it is unauthorized and produces a
    # simulation/playbook instead of the real engagement (audit b2c44b12). An
    # out-of-scope public target still demands per-target proof below (no
    # laundering). Import lazily: pentest_scope/authorization_gate import from this
    # module, so a top-level import would create a cycle.
    if target:
        try:
            from app.security.pentest_scope import matching_entry
            entry = matching_entry(target)
            if entry:
                org = entry.get("org", "") or "(declared engagement)"
                document = entry.get("document", "") or "(document on file)"
                return (
                    f"- **Authorizing party**: {org}\n"
                    f"- **Authorization basis**: {document}\n"
                    f"- **Scope**: `{target}` is covered by this declared, documented "
                    "engagement — per-target authorization EXISTS. This is authorized "
                    "work: proceed with the real assessment within the engagement scope.\n"
                    "- *Authorization is recorded per-target in the pentest scope file; "
                    "stay within the declared targets.*"
                )
        except Exception:
            pass

    # Real public-routable infrastructure is never swept under standing scope —
    # demand per-target proof regardless of deployment config. Reuse the single
    # posture classifier so this footer can never contradict the header (#5b).
    target_is_public = _authorization_posture(target) == "needs_proof"

    if s.roe_standing_authorization:
        org = s.roe_authorizing_org or "the operating organization"
        basis = s.roe_authorization_basis or "Standing operational authorization"
        lines = [
            f"- **Authorizing party**: {org}",
            f"- **Authorization basis**: {basis}",
        ]
        if target_is_public:
            lines.append(
                "- **Scope**: This target is real public-routable infrastructure. "
                "Standing authorization does NOT cover it — proceed only with "
                "per-target proof of ownership or written permission for THIS "
                "specific target."
            )
        else:
            lines.append(
                "- **Scope**: Limited to assets the operator actually owns or is "
                "contracted to test. Each target still requires its own valid "
                "authorization (ownership or written permission)."
            )
        lines.append(
            "- *Standing authorization is a deployment-level accountability record, "
            "not a waiver. The operator must hold valid authorization for each "
            "in-scope target.*"
        )
        return "\n".join(lines)
    return (
        "- **Authorizing party**: Self-attested by the task owner\n"
        "- **Authorization basis**: Not provided — no external document held on file\n"
        "- *The task owner is responsible for the validity of this authorization.*"
    )


def _render_pentest_roe(
    target: str, task_id: str, engagement_type: str, engagement_goal: str
) -> str:
    """Render pentest ROE template with parameters."""
    roe = _PENTEST_ROE_TEMPLATE.format(
        target=target,
        engagement_type=engagement_type or "General Security Assessment",
        engagement_goal=engagement_goal or "Maximum impact demonstration — prove highest achievable access level",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        task_id=(task_id[:8] if task_id else "unknown"),
        authorization_statement=_authorization_statement(target),
        authorization_block=_build_authorization_block(target),
    )
    logger.info("roe_generated", type="pentest", target=target, task_id=(task_id[:8] if task_id else ""))
    return roe


def _render_intel_roe(target: str, task_id: str, operation_type: str) -> str:
    """Render intel ROE template with parameters."""
    roe = _INTEL_ROE_TEMPLATE.format(
        target=target,
        operation_type=operation_type or "Intelligence Investigation",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        task_id=(task_id[:8] if task_id else "unknown"),
        authorization_block=_build_authorization_block(target),
    )
    logger.info("roe_generated", type="intel", target=target, task_id=(task_id[:8] if task_id else ""))
    return roe
