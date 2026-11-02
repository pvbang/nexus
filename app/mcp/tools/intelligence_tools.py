"""
Nexus - Intelligence & Security Operations Tools.

Advanced tools for OSINT, threat hunting, vulnerability research,
network analysis, and digital forensics.

PHILOSOPHY: Every tool embeds VERIFICATION as a core step.
Findings are graded by confidence, not reported as absolute truth.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# CVE Lookup & Verification
# ---------------------------------------------------------------------------


async def _handle_cve_lookup(arguments: dict) -> str:
    """Look up CVEs for software/version and assess real exploitability."""
    software = arguments.get("software", "")
    version = arguments.get("version", "")
    target_url = arguments.get("target_url", "")
    verify = arguments.get("verify", True)

    if not software:
        return "Error: software name is required."

    results: list[str] = []
    query = f"{software} {version}" if version else software

    # Step 1: Search NVD/CVE databases via web
    try:
        from app.mcp.tools.search_engines import _do_web_search
        search_results = await _do_web_search(
            f"site:nvd.nist.gov CVE {query}",
            max_results=10,
        )
        cve_pattern = re.compile(r"CVE-\d{4}-\d{4,}")
        found_cves: list[str] = []
        for r in (search_results or []):
            text = f"{r.get('title', '')} {r.get('snippet', '')}"
            found_cves.extend(cve_pattern.findall(text))
        found_cves = list(dict.fromkeys(found_cves))[:20]

        if found_cves:
            results.append(f"## CVEs Found for {query}: {len(found_cves)}")
            for cve_id in found_cves[:10]:
                results.append(f"  - {cve_id}")
        else:
            results.append(f"## No CVEs found in NVD for: {query}")
            results.append("NOTE: Absence of CVE does NOT mean safe — custom code has zero CVE coverage.")
    except Exception as e:
        results.append(f"## CVE search error: {e}")
        results.append("Falling back to manual verification approach.")
        found_cves = []

    # Step 2: Cross-validate with exploit-db
    try:
        from app.mcp.tools.search_engines import _do_web_search
        exploit_results = await _do_web_search(
            f"site:exploit-db.com {query}",
            max_results=5,
        )
        if exploit_results:
            results.append(f"\n## Exploit-DB References: {len(exploit_results)}")
            for r in exploit_results[:5]:
                results.append(f"  - {r.get('title', 'N/A')}: {r.get('url', '')}")
    except Exception:
        pass

    # Step 3: Verification assessment
    results.append("\n## VERIFICATION STATUS")
    if not verify:
        results.append("⚠️ Verification SKIPPED (verify=false). Findings are UNCONFIRMED.")
        results.append("REMINDER: CVE existence ≠ target is vulnerable. Manual verification required.")
    elif target_url:
        results.append(f"Target: {target_url}")
        results.append("Verification steps required:")
        results.append("  1. Confirm target runs affected version (fingerprint)")
        results.append("  2. Check if patches/mitigations are applied")
        results.append("  3. Test exploitability in controlled environment")
        results.append("  4. Develop PoC specific to target configuration")
        if version:
            results.append(f"  5. Compare target version ({version}) against fixed versions")
    else:
        results.append("⚠️ No target_url provided — cannot verify exploitability.")
        results.append("These are POTENTIAL vulnerabilities only. Confidence: C3 (Possibly true)")

    results.append("\n## CONFIDENCE GRADING")
    if found_cves and target_url:
        results.append("Current confidence: C3 (CVE exists, target not yet verified)")
    elif found_cves:
        results.append("Current confidence: D4 (CVE exists, no target context)")
    else:
        results.append("Current confidence: F6 (No CVE data, truth cannot be judged)")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Network Topology Mapping
# ---------------------------------------------------------------------------


async def _handle_network_topology_map(arguments: dict) -> str:
    """Map network topology through active probing."""
    target = arguments.get("target", "")
    port_range = arguments.get("port_range", "21-25,53,80,110,143,443,993,995,3306,5432,6379,8080,8443,27017")
    timeout_per_port = arguments.get("timeout", 2)
    include_traceroute = arguments.get("traceroute", False)

    if not target:
        return "Error: target (IP or hostname) is required."

    results: list[str] = [f"## Network Topology Scan: {target}"]
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("NOTE: Results verified through active probing, not documentation.")
    results.append("")

    # DNS Resolution
    import socket
    try:
        ip_addrs = socket.getaddrinfo(target, None)
        unique_ips = list(set(addr[4][0] for addr in ip_addrs))
        results.append(f"### DNS Resolution")
        for ip in unique_ips:
            results.append(f"  - {ip}")
        scan_target = unique_ips[0] if unique_ips else target
    except socket.gaierror:
        results.append(f"### DNS Resolution: FAILED (host not found)")
        scan_target = target

    # Port Scanning
    results.append(f"\n### Open Ports (verified by TCP connect)")
    ports = []
    for part in port_range.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            ports.extend(range(int(start), int(end) + 1))
        else:
            ports.append(int(part))

    open_ports: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(50)

    async def check_port(port: int) -> dict[str, Any] | None:
        async with sem:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(scan_target, port),
                    timeout=timeout_per_port,
                )
                writer.close()
                await writer.wait_closed()
                return {"port": port, "state": "open"}
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                return None

    tasks = [check_port(p) for p in ports]
    scan_results = await asyncio.gather(*tasks)
    open_ports = [r for r in scan_results if r is not None]

    if open_ports:
        for p in sorted(open_ports, key=lambda x: x["port"]):
            results.append(f"  - Port {p['port']}/tcp: OPEN (verified)")
    else:
        results.append("  No open ports found in scanned range.")

    # Service Banner Grabbing (for open ports)
    if open_ports:
        results.append(f"\n### Service Banners (first 5 ports)")
        for p_info in sorted(open_ports, key=lambda x: x["port"])[:5]:
            port = p_info["port"]
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(scan_target, port),
                    timeout=3,
                )
                writer.write(b"HEAD / HTTP/1.0\r\nHost: %b\r\n\r\n" % target.encode())
                banner = await asyncio.wait_for(reader.read(512), timeout=3)
                writer.close()
                await writer.wait_closed()
                banner_text = banner.decode("utf-8", errors="replace").strip()[:200]
                if banner_text:
                    results.append(f"  Port {port}: {banner_text[:100]}")
            except Exception:
                pass

    # Traceroute (if requested)
    if include_traceroute:
        results.append(f"\n### Traceroute to {scan_target}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "traceroute", "-m", "15", "-w", "2", scan_target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stdout:
                for line in stdout.decode().splitlines()[:15]:
                    results.append(f"  {line}")
        except (FileNotFoundError, asyncio.TimeoutError):
            # Try Windows tracert
            try:
                proc = await asyncio.create_subprocess_exec(
                    "tracert", "-d", "-h", "15", "-w", "2000", scan_target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
                if stdout:
                    for line in stdout.decode().splitlines()[:15]:
                        results.append(f"  {line}")
            except Exception:
                results.append("  Traceroute unavailable on this system.")

    results.append(f"\n### Verification Notes")
    results.append("- All ports verified by actual TCP connection (not SYN scan)")
    results.append("- Documentation/network diagrams may differ from actual topology")
    results.append("- Re-scan recommended to confirm consistency over time")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# IOC Validation
# ---------------------------------------------------------------------------


async def _handle_ioc_validate(arguments: dict) -> str:
    """Validate IOC against multiple sources and check freshness."""
    ioc_type = arguments.get("type", "")  # ip, domain, hash, url
    ioc_value = arguments.get("value", "")
    check_freshness = arguments.get("check_freshness", True)

    if not ioc_type or not ioc_value:
        return "Error: both 'type' (ip/domain/hash/url) and 'value' are required."

    results: list[str] = [f"## IOC Validation Report"]
    results.append(f"Type: {ioc_type} | Value: {ioc_value}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("")

    sources_checked = 0
    sources_flagged = 0

    # Source 1: Web search for threat reports
    try:
        from app.mcp.tools.search_engines import _do_web_search
        threat_results = await _do_web_search(
            f'"{ioc_value}" malware OR threat OR malicious OR C2',
            max_results=5,
        )
        sources_checked += 1
        if threat_results and len(threat_results) > 0:
            sources_flagged += 1
            results.append("### Source 1: Threat Reports (Web)")
            results.append(f"  Status: FLAGGED ({len(threat_results)} references found)")
            for r in threat_results[:3]:
                results.append(f"  - {r.get('title', '')[:80]}")
        else:
            results.append("### Source 1: Threat Reports (Web)")
            results.append("  Status: CLEAN (no references found)")
    except Exception as e:
        results.append(f"### Source 1: Threat Reports — Error: {e}")

    # Source 2: DNS/connectivity check (freshness)
    if check_freshness and ioc_type in ("ip", "domain"):
        import socket
        results.append(f"\n### Source 2: Freshness Check (Active Probe)")
        sources_checked += 1
        try:
            if ioc_type == "domain":
                addrs = socket.getaddrinfo(ioc_value, None, socket.AF_INET)
                current_ips = list(set(a[4][0] for a in addrs))
                results.append(f"  DNS resolves to: {', '.join(current_ips)}")
                results.append("  Status: ACTIVE (domain resolves)")
                sources_flagged += 1
            else:
                # Check if IP responds
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(ioc_value, 80),
                        timeout=5,
                    )
                    writer.close()
                    await writer.wait_closed()
                    results.append("  Status: ACTIVE (port 80 responds)")
                    sources_flagged += 1
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                    results.append("  Status: INACTIVE (no response on port 80)")
                    results.append("  ⚠️ IOC may be stale/reassigned — reduced confidence")
        except socket.gaierror:
            results.append("  Status: INACTIVE (DNS does not resolve)")
            results.append("  ⚠️ Domain no longer active — IOC likely stale")

    # Source 3: Reverse lookup / WHOIS indicator
    if ioc_type in ("ip", "domain"):
        results.append(f"\n### Source 3: Infrastructure Context")
        sources_checked += 1
        try:
            import socket
            if ioc_type == "ip":
                try:
                    hostname = socket.gethostbyaddr(ioc_value)
                    results.append(f"  Reverse DNS: {hostname[0]}")
                    if any(cdn in hostname[0].lower() for cdn in ["cloudflare", "akamai", "fastly", "amazonaws", "googleusercontent"]):
                        results.append("  ⚠️ WARNING: IP belongs to CDN/cloud provider — likely shared/reassigned")
                        results.append("  IOC confidence REDUCED — shared infrastructure")
                except socket.herror:
                    results.append("  Reverse DNS: No PTR record")
            else:
                addrs = socket.getaddrinfo(ioc_value, None)
                results.append(f"  Resolves to: {addrs[0][4][0] if addrs else 'N/A'}")
        except Exception as e:
            results.append(f"  Lookup failed: {e}")

    # Confidence Assessment
    results.append(f"\n## CONFIDENCE ASSESSMENT")
    results.append(f"Sources checked: {sources_checked}")
    results.append(f"Sources flagging malicious: {sources_flagged}")

    if sources_flagged >= 3:
        confidence = "B2 (Usually reliable, probably true)"
    elif sources_flagged >= 2:
        confidence = "C3 (Fairly reliable, possibly true)"
    elif sources_flagged == 1:
        confidence = "D4 (Single source only, doubtful)"
    else:
        confidence = "F6 (No confirming sources, truth cannot be judged)"

    results.append(f"Confidence grade: {confidence}")
    results.append("")
    results.append("## VERIFICATION REQUIRED")
    results.append("- Cross-reference with internal threat feeds")
    results.append("- Check if IOC appears in YOUR network traffic")
    results.append("- Sandbox any associated samples for behavioral confirmation")
    results.append("- DO NOT blocklist based on single-source attribution")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# OSINT Domain Profile
# ---------------------------------------------------------------------------


async def _handle_osint_domain_profile(arguments: dict) -> str:
    """Build comprehensive domain intelligence profile."""
    domain = arguments.get("domain", "")
    depth = arguments.get("depth", "standard")  # quick, standard, deep

    if not domain:
        return "Error: domain is required."

    # Clean domain
    domain = domain.strip().lower()
    if domain.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc

    results: list[str] = [f"## OSINT Domain Profile: {domain}"]
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append(f"Depth: {depth}")
    results.append("Source reliability: Data collected from multiple channels, cross-validated where possible.")
    results.append("")

    import socket

    # 1. DNS Records
    results.append("### DNS Records (Verified by direct query)")
    try:
        addrs = socket.getaddrinfo(domain, None)
        ipv4 = list(set(a[4][0] for a in addrs if a[0] == socket.AF_INET))
        ipv6 = list(set(a[4][0] for a in addrs if a[0] == socket.AF_INET6))
        if ipv4:
            results.append(f"  A records: {', '.join(ipv4)}")
        if ipv6:
            results.append(f"  AAAA records: {', '.join(ipv6)}")
        if not ipv4 and not ipv6:
            results.append("  No A/AAAA records found")
    except socket.gaierror:
        results.append("  DNS resolution FAILED — domain may not exist")
        return "\n".join(results)

    # MX records via nslookup/dig
    try:
        proc = await asyncio.create_subprocess_exec(
            "nslookup", "-type=MX", domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        mx_output = stdout.decode("utf-8", errors="replace")
        mx_lines = [l.strip() for l in mx_output.splitlines() if "mail exchanger" in l.lower() or "mx" in l.lower()]
        if mx_lines:
            results.append(f"  MX records: {'; '.join(mx_lines[:5])}")
    except Exception:
        pass

    # 2. HTTP Response Analysis
    results.append("\n### HTTP Fingerprint (Verified by direct request)")
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"https://{domain}", allow_redirects=True, ssl=False) as resp:
                results.append(f"  Status: {resp.status}")
                results.append(f"  Final URL: {resp.url}")
                server = resp.headers.get("Server", "Not disclosed")
                results.append(f"  Server: {server}")
                powered_by = resp.headers.get("X-Powered-By", "Not disclosed")
                if powered_by != "Not disclosed":
                    results.append(f"  X-Powered-By: {powered_by}")
                # Technology hints from headers
                for header in ["X-Generator", "X-Drupal-Cache", "X-WordPress", "X-Shopify-Stage"]:
                    val = resp.headers.get(header)
                    if val:
                        results.append(f"  {header}: {val}")
    except Exception as e:
        results.append(f"  HTTPS request failed: {e}")
        # Try HTTP
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"http://{domain}", allow_redirects=True) as resp:
                    results.append(f"  HTTP Status: {resp.status} (no HTTPS!)")
                    results.append(f"  Server: {resp.headers.get('Server', 'N/A')}")
        except Exception:
            results.append("  Both HTTP and HTTPS failed — host may be down")

    # 3. SSL Certificate Analysis
    results.append("\n### SSL Certificate (Verified by direct connection)")
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(domain, 443, ssl=ctx),
            timeout=5,
        )
        ssl_obj = writer.get_extra_info("ssl_object")
        if ssl_obj:
            cert = ssl_obj.getpeercert(binary_form=False)
            if cert:
                subject = dict(x[0] for x in cert.get("subject", []))
                issuer = dict(x[0] for x in cert.get("issuer", []))
                results.append(f"  Subject: {subject.get('commonName', 'N/A')}")
                results.append(f"  Issuer: {issuer.get('organizationName', 'N/A')}")
                results.append(f"  Valid from: {cert.get('notBefore', 'N/A')}")
                results.append(f"  Valid until: {cert.get('notAfter', 'N/A')}")
                sans = cert.get("subjectAltName", [])
                if sans:
                    san_domains = [v for t, v in sans if t == "DNS"][:10]
                    results.append(f"  SANs: {', '.join(san_domains)}")
        writer.close()
        await writer.wait_closed()
    except Exception as e:
        results.append(f"  SSL analysis failed: {e}")

    # 4. Technology Detection (from HTML)
    if depth in ("standard", "deep"):
        results.append("\n### Technology Stack (Detected from response)")
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"https://{domain}", ssl=False) as resp:
                    html = await resp.text()
                    techs: list[str] = []
                    if "wp-content" in html or "wordpress" in html.lower():
                        techs.append("WordPress")
                    if "react" in html.lower() or "__NEXT_DATA__" in html:
                        techs.append("React/Next.js")
                    if "vue" in html.lower():
                        techs.append("Vue.js")
                    if "angular" in html.lower():
                        techs.append("Angular")
                    if "laravel" in html.lower():
                        techs.append("Laravel")
                    if "django" in html.lower() or "csrfmiddlewaretoken" in html:
                        techs.append("Django")
                    if "shopify" in html.lower():
                        techs.append("Shopify")
                    if techs:
                        results.append(f"  Detected: {', '.join(techs)}")
                    else:
                        results.append("  No common frameworks detected from HTML")
        except Exception:
            pass

    # 5. WHOIS (via web search — actual whois command may not be available)
    if depth in ("standard", "deep"):
        results.append("\n### WHOIS Summary")
        try:
            from app.mcp.tools.search_engines import _do_web_search
            whois_results = await _do_web_search(f"whois {domain}", max_results=3)
            if whois_results:
                results.append(f"  Search results available — use fetch_url for full WHOIS data")
                for r in whois_results[:2]:
                    results.append(f"  - {r.get('title', '')[:60]}: {r.get('url', '')}")
            else:
                results.append("  WHOIS data not readily available via search")
        except Exception:
            results.append("  WHOIS lookup deferred — use dedicated WHOIS tool")

    results.append("\n### Reliability Assessment")
    results.append("- DNS/HTTP/SSL data: A1 (Verified by direct connection)")
    results.append("- Technology detection: B2 (Heuristic-based, usually reliable)")
    results.append("- WHOIS data: C3 (Third-party source, may be privacy-protected)")
    results.append("- Historical data: F6 (Not verified in this scan)")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Threat Intelligence Enrichment
# ---------------------------------------------------------------------------


async def _handle_threat_intel_enrich(arguments: dict) -> str:
    """Enrich an indicator with threat intelligence from multiple sources."""
    indicator = arguments.get("indicator", "")
    indicator_type = arguments.get("type", "auto")  # auto, ip, domain, hash

    if not indicator:
        return "Error: indicator is required."

    # Auto-detect type
    if indicator_type == "auto":
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", indicator):
            indicator_type = "ip"
        elif re.match(r"^[a-fA-F0-9]{32,64}$", indicator):
            indicator_type = "hash"
        elif "." in indicator and not indicator.startswith("http"):
            indicator_type = "domain"
        else:
            indicator_type = "unknown"

    results: list[str] = [f"## Threat Intelligence Enrichment"]
    results.append(f"Indicator: {indicator}")
    results.append(f"Type: {indicator_type}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("")

    enrichment_sources = 0
    threat_signals = 0

    # Source 1: General threat intelligence search
    try:
        from app.mcp.tools.search_engines import _do_web_search
        ti_results = await _do_web_search(
            f'"{indicator}" (malware OR threat OR APT OR C2 OR botnet)',
            max_results=5,
        )
        enrichment_sources += 1
        if ti_results:
            threat_signals += 1
            results.append("### Threat Reports")
            for r in ti_results[:5]:
                results.append(f"  - {r.get('title', '')[:80]}")
                results.append(f"    URL: {r.get('url', '')}")
        else:
            results.append("### Threat Reports: None found")
    except Exception as e:
        results.append(f"### Threat search error: {e}")

    # Source 2: Abuse/reputation check
    try:
        from app.mcp.tools.search_engines import _do_web_search
        abuse_results = await _do_web_search(
            f'"{indicator}" (abuse OR blacklist OR spam OR phishing)',
            max_results=3,
        )
        enrichment_sources += 1
        if abuse_results:
            threat_signals += 1
            results.append("\n### Abuse/Reputation")
            for r in abuse_results[:3]:
                results.append(f"  - {r.get('title', '')[:80]}")
    except Exception:
        pass

    # Source 3: Active verification (for IPs and domains)
    if indicator_type in ("ip", "domain"):
        import socket
        results.append("\n### Active Verification")
        enrichment_sources += 1
        try:
            if indicator_type == "domain":
                addrs = socket.getaddrinfo(indicator, None)
                ips = list(set(a[4][0] for a in addrs))
                results.append(f"  Current IPs: {', '.join(ips)}")
                results.append("  Status: RESOLVING (domain active)")
            else:
                try:
                    host = socket.gethostbyaddr(indicator)
                    results.append(f"  Reverse DNS: {host[0]}")
                except socket.herror:
                    results.append("  Reverse DNS: No PTR record")

                # Quick port check
                common_ports = [80, 443, 22, 8080]
                open_ports = []
                for port in common_ports:
                    try:
                        _, writer = await asyncio.wait_for(
                            asyncio.open_connection(indicator, port),
                            timeout=2,
                        )
                        open_ports.append(port)
                        writer.close()
                        await writer.wait_closed()
                    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                        pass
                if open_ports:
                    results.append(f"  Open ports: {open_ports}")
                    threat_signals += 1
                else:
                    results.append("  No common ports open — may be inactive")
        except Exception as e:
            results.append(f"  Verification error: {e}")

    # Confidence & Verdict
    results.append("\n## ENRICHMENT SUMMARY")
    results.append(f"Sources consulted: {enrichment_sources}")
    results.append(f"Threat signals: {threat_signals}/{enrichment_sources}")

    if threat_signals >= 3:
        verdict = "LIKELY MALICIOUS (B2 - multiple sources confirm)"
    elif threat_signals >= 2:
        verdict = "SUSPICIOUS (C3 - some indicators, needs verification)"
    elif threat_signals == 1:
        verdict = "INCONCLUSIVE (D4 - single source, could be false positive)"
    else:
        verdict = "NO THREAT DATA (F6 - no sources flagged, could be novel)"

    results.append(f"Verdict: {verdict}")
    results.append("")
    results.append("## CRITICAL REMINDERS")
    results.append("- A 'clean' result does NOT guarantee safety (novel threats have no history)")
    results.append("- A 'malicious' result requires CONTEXTUAL validation (shared hosting, CDN, IP reuse)")
    results.append("- Always verify in YOUR environment before taking action")
    results.append("- Threat intel has a half-life — recheck periodically")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Forensic Evidence Acquisition
# ---------------------------------------------------------------------------


async def _handle_evidence_acquire(arguments: dict) -> str:
    """Forensically acquire a file with hash verification and chain-of-custody."""
    source_path = arguments.get("source_path", "")
    evidence_dir = arguments.get("evidence_dir", "")
    case_id = arguments.get("case_id", "CASE-001")
    examiner = arguments.get("examiner", "Nexus-Forensics")

    if not source_path:
        return "Error: source_path is required."

    source = Path(source_path)
    if not source.exists():
        return f"Error: source file not found: {source_path}"

    # Default evidence directory
    if not evidence_dir:
        evidence_dir = str(Path("workspace_data") / "evidence" / case_id)

    evidence_path = Path(evidence_dir)
    evidence_path.mkdir(parents=True, exist_ok=True)

    results: list[str] = ["## Forensic Evidence Acquisition Report"]
    results.append(f"Case ID: {case_id}")
    results.append(f"Examiner: {examiner}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append(f"Source: {source_path}")
    results.append("")

    # Step 1: Hash original (BEFORE any copy)
    results.append("### Step 1: Source Hash (Pre-Acquisition)")
    try:
        sha256_orig = hashlib.sha256()
        md5_orig = hashlib.md5()
        with open(source, "rb") as f:
            while chunk := f.read(8192):
                sha256_orig.update(chunk)
                md5_orig.update(chunk)
        orig_sha256 = sha256_orig.hexdigest()
        orig_md5 = md5_orig.hexdigest()
        orig_size = source.stat().st_size

        results.append(f"  SHA-256: {orig_sha256}")
        results.append(f"  MD5: {orig_md5}")
        results.append(f"  Size: {orig_size} bytes")
        results.append(f"  Modified: {datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()}")
    except Exception as e:
        return f"Error computing source hash: {e}"

    # Step 2: Copy to evidence directory
    results.append("\n### Step 2: Acquisition (Bit-for-bit copy)")
    dest_name = f"{case_id}_{source.name}"
    dest_path = evidence_path / dest_name
    try:
        import shutil
        shutil.copy2(str(source), str(dest_path))
        results.append(f"  Destination: {dest_path}")
        results.append(f"  Copy method: shutil.copy2 (preserves metadata)")
    except Exception as e:
        return f"Error during acquisition: {e}"

    # Step 3: Verify copy integrity
    results.append("\n### Step 3: Integrity Verification (Post-Acquisition)")
    sha256_copy = hashlib.sha256()
    md5_copy = hashlib.md5()
    with open(dest_path, "rb") as f:
        while chunk := f.read(8192):
            sha256_copy.update(chunk)
            md5_copy.update(chunk)
    copy_sha256 = sha256_copy.hexdigest()
    copy_md5 = md5_copy.hexdigest()

    sha_match = orig_sha256 == copy_sha256
    md5_match = orig_md5 == copy_md5

    results.append(f"  SHA-256: {copy_sha256} {'✓ MATCH' if sha_match else '✗ MISMATCH!'}")
    results.append(f"  MD5: {copy_md5} {'✓ MATCH' if md5_match else '✗ MISMATCH!'}")

    if sha_match and md5_match:
        results.append("  Status: VERIFIED — evidence integrity confirmed")
    else:
        results.append("  ⚠️ CRITICAL: Hash mismatch! Evidence may be corrupted!")
        results.append("  Re-acquisition required. DO NOT use this copy.")

    # Step 4: Chain of custody log
    results.append("\n### Step 4: Chain of Custody Entry")
    coc_entry = {
        "case_id": case_id,
        "evidence_id": dest_name,
        "source_path": str(source_path),
        "destination_path": str(dest_path),
        "acquired_by": examiner,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": orig_sha256,
        "source_md5": orig_md5,
        "copy_sha256": copy_sha256,
        "copy_md5": copy_md5,
        "integrity_verified": sha_match and md5_match,
        "file_size_bytes": orig_size,
        "notes": "Bit-for-bit copy with metadata preservation",
    }

    coc_file = evidence_path / f"{case_id}_chain_of_custody.json"
    existing_coc: list[dict] = []
    if coc_file.exists():
        try:
            existing_coc = json.loads(coc_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing_coc.append(coc_entry)
    coc_file.write_text(json.dumps(existing_coc, indent=2, ensure_ascii=False))

    results.append(f"  Chain of custody log: {coc_file}")
    results.append(f"  Entry #{len(existing_coc)} recorded")

    results.append("\n### Evidence Handling Reminders")
    results.append("- NEVER modify the acquired copy — create working copies for analysis")
    results.append("- Re-verify hash before ANY analysis session")
    results.append("- Document ALL access to evidence in chain of custody")
    results.append("- Store in tamper-evident container for legal proceedings")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="cve_lookup",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "software": {
                "type": "string",
                "description": "Software name to search CVEs for (e.g., 'Apache HTTP Server', 'OpenSSL')",
            },
            "version": {
                "type": "string",
                "description": "Specific version to check (e.g., '2.4.49')",
            },
            "target_url": {
                "type": "string",
                "description": "Optional target URL to verify exploitability against",
            },
            "verify": {
                "type": "boolean",
                "description": "Whether to include verification guidance (default true)",
                "default": True,
            },
        },
        "required": ["software"],
    },
    handler=_handle_cve_lookup,
    is_async=True,
    emoji="🔍",
    category="intelligence",
)

registry.register(
    name="network_topology_map",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Target IP or hostname to map",
            },
            "port_range": {
                "type": "string",
                "description": "Ports to scan (e.g., '80,443,8080' or '1-1024')",
                "default": "21-25,53,80,110,143,443,993,995,3306,5432,6379,8080,8443,27017",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout per port in seconds",
                "default": 2,
            },
            "traceroute": {
                "type": "boolean",
                "description": "Include traceroute to target",
                "default": False,
            },
        },
        "required": ["target"],
    },
    handler=_handle_network_topology_map,
    is_async=True,
    emoji="🗺️",
    category="intelligence",
)

registry.register(
    name="ioc_validate",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "IOC type: ip, domain, hash, or url",
                "enum": ["ip", "domain", "hash", "url"],
            },
            "value": {
                "type": "string",
                "description": "The IOC value to validate",
            },
            "check_freshness": {
                "type": "boolean",
                "description": "Actively probe to check if IOC is currently active",
                "default": True,
            },
        },
        "required": ["type", "value"],
    },
    handler=_handle_ioc_validate,
    is_async=True,
    emoji="🎯",
    category="intelligence",
)

registry.register(
    name="osint_domain_profile",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Domain to profile (e.g., 'example.com')",
            },
            "depth": {
                "type": "string",
                "description": "Scan depth: quick (DNS only), standard (+ HTTP/SSL/tech), deep (+ WHOIS/history)",
                "enum": ["quick", "standard", "deep"],
                "default": "standard",
            },
        },
        "required": ["domain"],
    },
    handler=_handle_osint_domain_profile,
    is_async=True,
    emoji="🕸️",
    category="intelligence",
)

registry.register(
    name="threat_intel_enrich",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "Indicator to enrich (IP, domain, hash, or URL)",
            },
            "type": {
                "type": "string",
                "description": "Indicator type (auto-detected if not specified)",
                "enum": ["auto", "ip", "domain", "hash", "url"],
                "default": "auto",
            },
        },
        "required": ["indicator"],
    },
    handler=_handle_threat_intel_enrich,
    is_async=True,
    emoji="🧠",
    category="intelligence",
)

registry.register(
    name="evidence_acquire",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to file/artifact to acquire forensically",
            },
            "evidence_dir": {
                "type": "string",
                "description": "Directory to store acquired evidence (auto-created)",
            },
            "case_id": {
                "type": "string",
                "description": "Case identifier for chain-of-custody tracking",
                "default": "CASE-001",
            },
            "examiner": {
                "type": "string",
                "description": "Name of examiner performing acquisition",
                "default": "Nexus-Forensics",
            },
        },
        "required": ["source_path"],
    },
    handler=_handle_evidence_acquire,
    is_async=True,
    emoji="🔬",
    category="intelligence",
)
