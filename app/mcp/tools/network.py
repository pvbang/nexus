"""
MekongAI Nexus - Network & Security Tools.

Tools for network reconnaissance, port scanning, TCP connections,
SSH remote execution, and DNS lookups.

WARNING: These tools can interact with remote servers.
Use responsibly and only on systems you own or have permission to test.
"""

import asyncio
import json
import socket
import struct
import time
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

TOOLSET = "network"


# ============================================================
# Tool 1: port_scan — TCP port scanner
# ============================================================

async def handle_port_scan(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Scan TCP ports on a target host.
    
    Scans specified ports or common ports on a target.
    Uses async socket connections for speed.
    """
    host = params.get("host", "").strip()
    if not host:
        return {"success": False, "output": tool_error("host is required")}
    
    # Parse ports
    ports_param = params.get("ports", "")
    if isinstance(ports_param, str) and ports_param:
        # Parse comma-separated or range: "80,443,8080" or "1-1024"
        ports = _parse_ports(ports_param)
    elif isinstance(ports_param, list):
        ports = [int(p) for p in ports_param]
    else:
        # Default common ports
        ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995,
                 1433, 1521, 3000, 3306, 3389, 5432, 5900, 6379, 8000,
                 8080, 8443, 9090, 9200, 27017]
    
    timeout = min(params.get("timeout", 2), 10)  # Max 10s per port
    max_ports = 200  # Safety limit
    ports = ports[:max_ports]
    
    open_ports = []
    closed_count = 0
    
    async def scan_port(port: int) -> dict | None:
        try:
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            
            # Try to get service banner
            service = _guess_service(port)
            return {"port": port, "state": "open", "service": service}
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return None
    
    # Scan in batches of 50 for efficiency
    for i in range(0, len(ports), 50):
        batch = ports[i:i+50]
        results = await asyncio.gather(*[scan_port(p) for p in batch])
        for r in results:
            if r:
                open_ports.append(r)
            else:
                closed_count += 1
    
    output = {
        "host": host,
        "total_scanned": len(ports),
        "open_ports": open_ports,
        "open_count": len(open_ports),
        "closed_count": closed_count,
    }
    
    # Format readable output
    lines = [f"Port scan results for {host}:"]
    for p in open_ports:
        lines.append(f"  {p['port']}/tcp  OPEN  ({p['service']})")
    lines.append(f"\n{len(open_ports)} open / {closed_count} closed / {len(ports)} total scanned")
    
    return {"success": True, "output": "\n".join(lines), "data": output}


def _parse_ports(ports_str: str) -> list[int]:
    """Parse port specification: '80,443,8080' or '1-1024' or mixed."""
    ports = []
    for part in ports_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            try:
                ports.extend(range(int(start), int(end) + 1))
            except ValueError:
                pass
        else:
            try:
                ports.append(int(part))
            except ValueError:
                pass
    return sorted(set(p for p in ports if 1 <= p <= 65535))


def _guess_service(port: int) -> str:
    """Guess service name from port number."""
    services = {
        21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns",
        80: "http", 110: "pop3", 143: "imap", 443: "https", 445: "smb",
        993: "imaps", 995: "pop3s", 1433: "mssql", 1521: "oracle",
        3000: "grafana/node", 3306: "mysql", 3389: "rdp", 5432: "postgresql",
        5900: "vnc", 6379: "redis", 8000: "http-alt", 8080: "http-proxy",
        8443: "https-alt", 9090: "prometheus", 9200: "elasticsearch",
        27017: "mongodb",
    }
    return services.get(port, "unknown")


registry.register(
    name="port_scan",
    toolset=TOOLSET,
    handler=handle_port_scan,
    description="Scan TCP ports on a target host to discover open services",
    parameters={
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Target hostname or IP address"},
            "ports": {"type": "string", "description": "Ports to scan: '80,443,8080' or '1-1024' or leave empty for common ports"},
            "timeout": {"type": "number", "description": "Timeout per port in seconds (default: 2, max: 10)"},
        },
        "required": ["host"],
    },
    emoji="🔍",
    category="network",
)


# ============================================================
# Tool 2: tcp_connect — Raw TCP connection
# ============================================================

async def handle_tcp_connect(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Make a raw TCP connection to send/receive data.
    
    Useful for interacting with Redis, Memcached, SMTP, or any TCP service.
    """
    host = params.get("host", "").strip()
    port = params.get("port")
    if not host or not port:
        return {"success": False, "output": tool_error("host and port are required")}
    
    port = int(port)
    data_to_send = params.get("data", "")
    timeout = min(params.get("timeout", 5), 30)
    
    try:
        conn = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        
        response = ""
        
        # Try to read banner first
        try:
            banner = await asyncio.wait_for(reader.read(4096), timeout=2)
            response = banner.decode(errors="replace")
        except asyncio.TimeoutError:
            pass
        
        # Send data if provided
        if data_to_send:
            if isinstance(data_to_send, str):
                data_bytes = (data_to_send + "\r\n").encode()
            else:
                data_bytes = str(data_to_send).encode() + b"\r\n"
            
            writer.write(data_bytes)
            await writer.drain()
            
            # Read response
            try:
                resp_data = await asyncio.wait_for(reader.read(8192), timeout=timeout)
                response += resp_data.decode(errors="replace")
            except asyncio.TimeoutError:
                response += "\n(read timeout)"
        
        writer.close()
        await writer.wait_closed()
        
        # Truncate long responses
        if len(response) > 10000:
            response = response[:10000] + "\n... (truncated)"
        
        return {
            "success": True,
            "output": f"TCP {host}:{port} — Connected\n{response}",
            "data": {"host": host, "port": port, "response": response},
        }
        
    except asyncio.TimeoutError:
        return {"success": False, "output": f"Connection to {host}:{port} timed out ({timeout}s)"}
    except ConnectionRefusedError:
        return {"success": False, "output": f"Connection to {host}:{port} refused"}
    except OSError as e:
        return {"success": False, "output": f"TCP error: {e}"}


registry.register(
    name="tcp_connect",
    toolset=TOOLSET,
    handler=handle_tcp_connect,
    description="Make raw TCP connection to send/receive data (Redis, SMTP, etc.)",
    parameters={
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Target hostname or IP"},
            "port": {"type": "integer", "description": "Target port number"},
            "data": {"type": "string", "description": "Data to send after connecting (optional)"},
            "timeout": {"type": "number", "description": "Connection timeout in seconds (default: 5)"},
        },
        "required": ["host", "port"],
    },
    emoji="🔌",
    category="network",
)


# ============================================================
# Tool 3: ssh_exec — Execute command on remote server via SSH
# ============================================================

async def handle_ssh_exec(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute a command on a remote server via SSH.
    
    Requires paramiko (optional dependency).
    Supports password auth and key-based auth.
    """
    host = params.get("host", "").strip()
    command = params.get("command", "").strip()
    if not host or not command:
        return {"success": False, "output": tool_error("host and command are required")}
    
    username = params.get("username", "root")
    password = params.get("password", "")
    port = int(params.get("port", 22))
    timeout = min(params.get("timeout", 30), 60)
    key_path = params.get("key_path", "")
    
    try:
        import paramiko
    except ImportError:
        return {"success": False, "output": tool_error(
            "paramiko not installed. Run: pip install paramiko"
        )}
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": timeout,
        }
        
        if key_path:
            try:
                key = paramiko.RSAKey.from_private_key_file(key_path)
                connect_kwargs["pkey"] = key
            except Exception as ke:
                return {"success": False, "output": f"SSH key error: {ke}"}
        elif password:
            connect_kwargs["password"] = password
        
        # Run in executor to not block event loop
        loop = asyncio.get_event_loop()
        
        def _ssh_run():
            ssh.connect(**connect_kwargs)
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
            out = stdout.read().decode(errors="replace")[:10000]
            err = stderr.read().decode(errors="replace")[:2000]
            exit_code = stdout.channel.recv_exit_status()
            ssh.close()
            return out, err, exit_code
        
        out, err, exit_code = await asyncio.wait_for(
            loop.run_in_executor(None, _ssh_run),
            timeout=timeout + 5,
        )
        
        output_lines = [f"SSH {username}@{host}:{port} $ {command}"]
        output_lines.append(f"Exit code: {exit_code}")
        if out:
            output_lines.append(f"\nSTDOUT:\n{out}")
        if err:
            output_lines.append(f"\nSTDERR:\n{err}")
        
        return {
            "success": exit_code == 0,
            "output": "\n".join(output_lines),
            "data": {
                "host": host, "command": command,
                "stdout": out, "stderr": err,
                "exit_code": exit_code,
            },
        }
        
    except Exception as e:
        return {"success": False, "output": f"SSH error: {e}"}


def _check_paramiko() -> bool:
    try:
        import paramiko  # noqa: F401
        return True
    except ImportError:
        return False


registry.register(
    name="ssh_exec",
    toolset=TOOLSET,
    handler=handle_ssh_exec,
    description="Execute command on remote server via SSH (password or key auth)",
    parameters={
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "SSH server hostname or IP"},
            "command": {"type": "string", "description": "Shell command to execute"},
            "username": {"type": "string", "description": "SSH username (default: root)"},
            "password": {"type": "string", "description": "SSH password (optional if using key)"},
            "port": {"type": "integer", "description": "SSH port (default: 22)"},
            "key_path": {"type": "string", "description": "Path to SSH private key file (optional)"},
            "timeout": {"type": "integer", "description": "Command timeout in seconds (default: 30)"},
        },
        "required": ["host", "command"],
    },
    emoji="🔐",
    category="network",
    check_fn=_check_paramiko,
)


# ============================================================
# Tool 4: dns_lookup — DNS queries and reconnaissance
# ============================================================

async def handle_dns_lookup(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Perform DNS lookups: A, AAAA, MX, NS, TXT, CNAME, SOA, reverse DNS.
    
    Uses Python's built-in socket for basic lookups.
    Handles Docker environments where DNS may be limited.
    """
    domain = params.get("domain", "").strip()
    if not domain:
        return {"success": False, "output": tool_error("domain is required")}
    
    record_type = params.get("type", "A").upper()
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    
    results: dict[str, Any] = {}
    
    try:
        if record_type in ("A", "ALL"):
            try:
                addrs = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET)
                    ),
                    timeout=10.0,
                )
                results["A"] = list(set(addr[4][0] for addr in addrs))
            except (socket.gaierror, asyncio.TimeoutError, OSError) as e:
                results["A"] = []
                results.setdefault("_errors", []).append(f"A lookup failed: {e}")
        
        if record_type in ("AAAA", "ALL"):
            try:
                addrs = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET6)
                    ),
                    timeout=10.0,
                )
                results["AAAA"] = list(set(addr[4][0] for addr in addrs))
            except (socket.gaierror, asyncio.TimeoutError, OSError):
                results["AAAA"] = []
        
        if record_type in ("PTR", "REVERSE"):
            # Reverse DNS
            ip = params.get("ip", domain)
            try:
                hostname, _, _ = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: socket.gethostbyaddr(ip)
                    ),
                    timeout=10.0,
                )
                results["PTR"] = hostname
            except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
                results["PTR"] = "(no reverse DNS)"
        
        if record_type in ("MX", "NS", "TXT", "CNAME", "SOA", "ALL"):
            # Use dnspython if available, otherwise fallback
            try:
                import dns.resolver
                resolver = dns.resolver.Resolver()
                resolver.timeout = 5
                resolver.lifetime = 10
                
                for rtype in (["MX", "NS", "TXT", "CNAME", "SOA"] if record_type == "ALL" else [record_type]):
                    try:
                        answers = await asyncio.wait_for(
                            loop.run_in_executor(
                                None, lambda rt=rtype: resolver.resolve(domain, rt)
                            ),
                            timeout=10.0,
                        )
                        results[rtype] = [str(r) for r in answers]
                    except (asyncio.TimeoutError, Exception):
                        results[rtype] = []
                        
            except ImportError:
                # dnspython not available, basic lookup only
                if not results or all(v == [] for k, v in results.items() if k != "_errors"):
                    results["note"] = "dnspython not installed — only A/AAAA/PTR available. pip install dnspython for MX/NS/TXT/etc."
                    # Do basic A lookup as fallback
                    try:
                        addrs = await asyncio.wait_for(
                            loop.run_in_executor(
                                None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET)
                            ),
                            timeout=10.0,
                        )
                        results["A"] = list(set(addr[4][0] for addr in addrs))
                    except (socket.gaierror, asyncio.TimeoutError, OSError):
                        results["A"] = []
        
        # Remove internal error tracking from output data
        errors = results.pop("_errors", [])
        
        # Format output
        lines = [f"DNS lookup for {domain}:"]
        for rtype, values in results.items():
            if rtype == "note":
                lines.append(f"\n⚠️ {values}")
            elif isinstance(values, list):
                if values:
                    for v in values:
                        lines.append(f"  {rtype}: {v}")
                else:
                    lines.append(f"  {rtype}: (no records)")
            else:
                lines.append(f"  {rtype}: {values}")
        
        if errors:
            lines.append(f"\n⚠️ Partial failures: {'; '.join(errors)}")
        
        # Consider success even with partial results (some records not found is normal)
        return {
            "success": True,
            "output": "\n".join(lines),
            "data": {"domain": domain, "records": results},
        }
        
    except Exception as e:
        return {"success": False, "output": f"DNS error for {domain}: {type(e).__name__}: {e}"}


registry.register(
    name="dns_lookup",
    toolset=TOOLSET,
    handler=handle_dns_lookup,
    description="DNS lookup: A, AAAA, MX, NS, TXT, CNAME, SOA, reverse DNS",
    parameters={
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "Domain name to lookup"},
            "type": {"type": "string", "description": "Record type: A, AAAA, MX, NS, TXT, CNAME, SOA, PTR, ALL (default: A)"},
            "ip": {"type": "string", "description": "IP address for reverse DNS (PTR) lookup"},
        },
        "required": ["domain"],
    },
    emoji="🌐",
    category="network",
)


# ============================================================
# Tool 5: network_probe — Ping, traceroute, service fingerprint
# ============================================================

async def handle_network_probe(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Network probe: ping, service fingerprint, or whois lookup.
    
    Modes:
    - ping: Check if host is reachable (TCP ping to port 80/443)
    - fingerprint: Connect to port and read banner/headers
    - whois: Domain WHOIS lookup (requires python-whois)
    """
    host = params.get("host", "").strip()
    if not host:
        return {"success": False, "output": tool_error("host is required")}
    
    mode = params.get("mode", "fingerprint").lower()
    port = int(params.get("port", 80))
    timeout = min(params.get("timeout", 5), 15)
    
    if mode == "ping":
        # TCP ping (more reliable than ICMP which requires root)
        ports_to_try = [port, 443, 80, 22]
        results = []
        
        for p in ports_to_try:
            start = time.perf_counter()
            try:
                conn = asyncio.open_connection(host, p)
                reader, writer = await asyncio.wait_for(conn, timeout=timeout)
                elapsed = (time.perf_counter() - start) * 1000
                writer.close()
                await writer.wait_closed()
                results.append({"port": p, "reachable": True, "latency_ms": round(elapsed, 1)})
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                results.append({"port": p, "reachable": False})
        
        reachable = any(r["reachable"] for r in results)
        lines = [f"TCP ping {host}:"]
        for r in results:
            if r["reachable"]:
                lines.append(f"  Port {r['port']}: ✅ reachable ({r['latency_ms']}ms)")
            else:
                lines.append(f"  Port {r['port']}: ❌ unreachable")
        
        return {
            "success": reachable,
            "output": "\n".join(lines),
            "data": {"host": host, "results": results, "reachable": reachable},
        }
    
    elif mode == "fingerprint":
        # Service fingerprint: connect and read banner + HTTP headers
        try:
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            
            # Try to read banner
            banner = ""
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=2)
                banner = data.decode(errors="replace").strip()
            except asyncio.TimeoutError:
                pass
            
            # If no banner, try HTTP
            if not banner:
                writer.write(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
                await writer.drain()
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                    banner = data.decode(errors="replace").strip()
                except asyncio.TimeoutError:
                    banner = "(no response)"
            
            writer.close()
            await writer.wait_closed()
            
            # Parse headers if HTTP
            server = ""
            if "HTTP/" in banner:
                for line in banner.split("\r\n"):
                    if line.lower().startswith("server:"):
                        server = line.split(":", 1)[1].strip()
            
            return {
                "success": True,
                "output": f"Fingerprint {host}:{port}:\n  Banner: {banner[:500]}\n  Server: {server or 'unknown'}",
                "data": {"host": host, "port": port, "banner": banner[:2000], "server": server},
            }
            
        except Exception as e:
            return {"success": False, "output": f"Fingerprint failed: {e}"}
    
    elif mode == "whois":
        try:
            import whois as python_whois
            loop = asyncio.get_event_loop()
            w = await loop.run_in_executor(None, lambda: python_whois.whois(host))
            
            info = {
                "domain": str(w.domain_name) if w.domain_name else host,
                "registrar": str(w.registrar) if w.registrar else "",
                "creation_date": str(w.creation_date) if w.creation_date else "",
                "expiration_date": str(w.expiration_date) if w.expiration_date else "",
                "name_servers": [str(ns) for ns in (w.name_servers or [])],
                "status": [str(s) for s in (w.status or [])],
            }
            
            lines = [f"WHOIS {host}:"]
            for k, v in info.items():
                lines.append(f"  {k}: {v}")
            
            return {"success": True, "output": "\n".join(lines), "data": info}
            
        except ImportError:
            return {"success": False, "output": "python-whois not installed. pip install python-whois"}
        except Exception as e:
            return {"success": False, "output": f"WHOIS error: {e}"}
    
    else:
        return {"success": False, "output": f"Unknown mode: {mode}. Use: ping, fingerprint, whois"}


registry.register(
    name="network_probe",
    toolset=TOOLSET,
    handler=handle_network_probe,
    description="Network probe: TCP ping, service fingerprint, or WHOIS lookup",
    parameters={
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Target hostname or IP"},
            "mode": {"type": "string", "description": "Mode: ping, fingerprint, whois (default: fingerprint)"},
            "port": {"type": "integer", "description": "Port for fingerprint/ping (default: 80)"},
            "timeout": {"type": "number", "description": "Timeout in seconds (default: 5)"},
        },
        "required": ["host"],
    },
    emoji="📡",
    category="network",
)
