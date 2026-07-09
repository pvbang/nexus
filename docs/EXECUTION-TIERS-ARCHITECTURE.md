# Nexus Execution Tiers — Full Architecture

**Problem:** Agents chi co sandbox Python, khong the: download APK, chay jadx,
deploy Frida, capture traffic, exploit cloud. 17 turns vo ich vi "khong co tay chan".

**Solution:** 4-tier execution model. Agent tu chon tier phu hop. Co the tu tao
tools/capabilities khi thieu.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     AGENT (LLM + Tool Calling)                  │
│                                                                 │
│  "I need to decompile APK"                                     │
│  → checks Infrastructure Map                                    │
│  → sees: Docker Toolkit AVAILABLE, jadx READY                  │
│  → calls: docker_exec(image="nexus-security", cmd="jadx ...")  │
│                                                                 │
│  "I need to run Frida on device"                               │
│  → sees: Device Bridge AVAILABLE (Pixel 6a, rooted)            │
│  → calls: device_exec(cmd="frida -U -l script.js com.target")  │
│                                                                 │
│  "I need tool X but it doesn't exist"                          │
│  → calls: scaffold_custom_tool_module(...)                     │
│  → or: docker_exec(image="custom", cmd="...")                  │
└─────────────────────────────────────────────────────────────────┘
         │            │              │              │
    ┌────▼────┐  ┌───▼────┐   ┌────▼────┐   ┌────▼────┐
    │ Tier 0  │  │ Tier 1 │   │ Tier 2  │   │ Tier 3  │
    │In-Proc  │  │Docker  │   │ Device  │   │ Cloud   │
    │Python   │  │Sandbox │   │ Bridge  │   │  Infra  │
    │(current)│  │(new)   │   │ (new)   │   │ (new)   │
    └─────────┘  └────────┘   └─────────┘   └─────────┘
```

---

## Tier 0: In-Process (existing)

**What:** Python execution trong process hien tai.
**Tools:** execute_code, read_file, write_file, web_search, http_request
**Limits:** No binary execution, limited network, no root access
**Use for:** Code generation, analysis, file manipulation, web research

---

## Tier 1: Docker Security Toolkit

**What:** Pre-built Docker container voi toan bo security tools.
**Access:** Tool `docker_exec(image, command, workdir, timeout)`

### Docker Image: `nexus-security-toolkit`

```dockerfile
FROM ubuntu:22.04

# Core security tools
RUN apt-get update && apt-get install -y \
    openjdk-17-jdk \
    python3 python3-pip \
    wget curl git unzip \
    nmap masscan \
    sqlmap \
    && rm -rf /var/lib/apt/lists/*

# Android RE tools
RUN wget -O /opt/jadx.zip https://github.com/skylot/jadx/releases/download/v1.5.0/jadx-1.5.0.zip \
    && unzip /opt/jadx.zip -d /opt/jadx && rm /opt/jadx.zip
RUN wget -O /opt/apktool.jar https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar

# Frida tools (for connecting to remote device)
RUN pip3 install frida-tools objection mitmproxy

# Web security
RUN go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
RUN go install github.com/ffuf/ffuf/v2@latest

# Network tools
RUN apt-get install -y tcpdump tshark netcat-openbsd socat dnsutils

# Exploitation
RUN pip3 install pwntools impacket

ENV PATH="/opt/jadx/bin:/root/go/bin:${PATH}"
WORKDIR /workspace
```

### Tool Interface:

```python
@registry.register(toolset="docker", category="execution")
async def docker_exec(
    command: str,
    image: str = "nexus-security-toolkit",
    workdir: str = "/workspace",
    timeout: int = 300,
    mount_workspace: bool = True,
) -> dict:
    """Execute command in Docker container with security tools.

    Available images:
    - nexus-security-toolkit: jadx, apktool, nuclei, frida-tools, nmap, sqlmap
    - nexus-android-re: jadx, apktool, dex2jar, androguard, smali/baksmali
    - nexus-exploit-dev: pwntools, metasploit, impacket, responder
    - nexus-network: mitmproxy, burpsuite-cli, tcpdump, wireshark-cli

    Examples:
    - docker_exec("jadx -d /workspace/output app.apk")
    - docker_exec("nuclei -u https://target.com -t cves/")
    - docker_exec("frida -H 192.168.1.100 -l hook.js com.target")
    - docker_exec("apktool d -f app.apk -o /workspace/decompiled")
    """
```

### Workspace Mounting:
- Agent's `_work/` directory mounted as `/workspace` in container
- Files created in container appear in agent workspace
- APK placed in `_work/` → jadx can process it → output back in `_work/`

---

## Tier 2: Remote Device Bridge

**What:** ADB/SSH connection toi rooted Android device.
**Access:** Tool `device_exec(command, device_id)` + `device_push/pull`

### Setup (one-time):
- Pixel 6a/emulator with Magisk + frida-server running
- ADB over network (adb connect 192.168.x.x:5555)
- Or SSH via Termux

### Tool Interface:

```python
@registry.register(toolset="device", category="mobile-testing")
async def device_exec(
    command: str,
    device_id: str = "default",
    as_root: bool = False,
    timeout: int = 60,
) -> dict:
    """Execute command on connected Android device via ADB.

    Device must be configured in NEXUS_DEVICES env var.
    Supports: frida, logcat, am, pm, dumpsys, shell commands.

    Examples:
    - device_exec("frida -U -l /data/local/tmp/hook.js com.target")
    - device_exec("pm list packages | grep reelshort")
    - device_exec("logcat -d -s 'WebView'", timeout=10)
    - device_exec("dumpsys activity activities | grep mResumedActivity")
    """

@registry.register(toolset="device", category="mobile-testing")
async def device_push(local_path: str, remote_path: str) -> dict:
    """Push file from workspace to device."""

@registry.register(toolset="device", category="mobile-testing")
async def device_pull(remote_path: str, local_path: str) -> dict:
    """Pull file from device to workspace."""

@registry.register(toolset="device", category="mobile-testing")
async def frida_run(
    script_path: str,
    package: str,
    spawn: bool = True,
    timeout: int = 30,
) -> dict:
    """Run Frida script against app on connected device.

    Pushes script to device, attaches/spawns app, captures output.
    Returns: hook output, intercepted data, errors.
    """
```

### Device Configuration:
```bash
# .env
NEXUS_DEVICES='[
  {"id": "pixel6a", "type": "adb", "address": "192.168.1.100:5555", "rooted": true},
  {"id": "emulator", "type": "adb", "address": "localhost:5554", "rooted": true}
]'
```

---

## Tier 3: Cloud Infrastructure

**What:** Cloud CLIs configured cho offensive operations.
**Access:** Tools `cloud_exec(provider, command)` + cloud-specific tools

### Providers:

```python
@registry.register(toolset="cloud", category="infrastructure")
async def cloud_exec(
    provider: str,  # "alibaba" | "aws" | "gcp"
    command: str,
    timeout: int = 60,
) -> dict:
    """Execute cloud CLI command.

    Requires provider credentials configured in env.
    Runs in isolated Docker container with cloud CLIs.

    Examples:
    - cloud_exec("alibaba", "aliyun ecs DescribeInstances")
    - cloud_exec("aws", "aws s3 ls s3://target-bucket")
    - cloud_exec("alibaba", "aliyun sts GetCallerIdentity")
    """

@registry.register(toolset="cloud", category="infrastructure")
async def cloud_provision(
    provider: str,
    resource_type: str,  # "sandbox_vm" | "network" | "storage"
    config: dict = None,
) -> dict:
    """Provision temporary cloud resource for testing.

    Creates short-lived (1h TTL) resources for:
    - Sandbox VM (attack box)
    - Temporary storage (evidence collection)
    - Network endpoint (C2 simulation, callback receiver)

    Auto-destroyed after TTL expires.
    """
```

### Alibaba Cloud Specific:
```python
@registry.register(toolset="alibaba", category="cloud-security")
async def alibaba_get_metadata(
    resource: str = "sts_token",  # "sts_token" | "instance_meta" | "ram_policy"
) -> dict:
    """Get Alibaba Cloud metadata/tokens for security testing.

    When credentials are configured:
    - Retrieve STS temporary tokens
    - Check RAM policy permissions
    - Enumerate accessible resources
    - Test for privilege escalation paths
    """
```

---

## Tier 4: Self-Evolving Tool Factory

**What:** Agent CREATES tools at runtime khi thieu capability.
**This is the KEY differentiator — Nexus can grow itself.**

### Pattern:

```
Agent thinks: "I need to analyze .dex files but no tool exists"
    │
    ▼
Agent calls: scaffold_custom_tool_module(
    module_name="dex_analyzer",
    tool_name="analyze_dex",
    description="Analyze .dex files for classes, methods, strings",
    parameters={...},
    implementation_hint="Use androguard library",
    install_deps=["androguard"],
)
    │
    ▼ Tool created + loaded instantly
    │
Agent calls: analyze_dex(dex_path="/workspace/classes.dex")
```

### Enhanced Self-Evolve (upgrade tu hien tai):

```python
@registry.register(toolset="meta", category="self-evolve")
async def create_execution_capability(
    name: str,
    description: str,
    docker_image: str = "nexus-security-toolkit",
    command_template: str = "",
    install_commands: list[str] = None,
) -> dict:
    """Create a new execution capability by wrapping Docker commands.

    Instead of writing full Python tool code, this creates a tool that
    runs a specific command pattern in a Docker container.

    Examples:
    - create_execution_capability(
        name="decompile_apk",
        description="Decompile APK with jadx",
        command_template="jadx -d /workspace/output {apk_path}",
      )
    - create_execution_capability(
        name="scan_nuclei",
        description="Run nuclei vulnerability scanner",
        command_template="nuclei -u {target_url} -t {template_dir}",
      )

    After creation, the tool is immediately available for use.
    """
```

### Composite Tool Creation (chain cua tools):

```python
@registry.register(toolset="meta", category="self-evolve")
async def create_workflow_tool(
    name: str,
    description: str,
    steps: list[dict],
) -> dict:
    """Create a multi-step workflow as a single tool.

    Each step references existing tools + Docker commands.

    Example:
    - create_workflow_tool(
        name="full_apk_audit",
        description="Download, decompile, and analyze APK",
        steps=[
            {"tool": "docker_exec", "args": {"command": "wget {apk_url} -O /workspace/app.apk"}},
            {"tool": "docker_exec", "args": {"command": "jadx -d /workspace/jadx_out /workspace/app.apk"}},
            {"tool": "docker_exec", "args": {"command": "grep -r 'API_KEY\\|SECRET\\|password' /workspace/jadx_out/"}},
            {"tool": "read_file", "args": {"path": "{workspace}/jadx_out/AndroidManifest.xml"}},
        ]
      )
    """
```

---

## How Agent Chooses Tier

Decision tree (embedded in Infrastructure Map):

```
Need to execute something?
    │
    ├── Pure Python/analysis? → Tier 0 (execute_code)
    │
    ├── Need security tools (jadx, nmap, frida-tools)?
    │   ├── Docker available? → Tier 1 (docker_exec)
    │   └── Docker NOT available? → Tier 4 (create tool that downloads+runs)
    │
    ├── Need to interact with mobile device?
    │   ├── Device connected? → Tier 2 (device_exec, frida_run)
    │   └── No device? → Tier 1 (emulator in Docker) or document for user
    │
    ├── Need cloud access?
    │   ├── Credentials configured? → Tier 3 (cloud_exec)
    │   └── No credentials? → Self-bootstrap (create_cloud_sandbox) or ask user
    │
    └── Need capability that doesn't exist?
        └── Tier 4 (scaffold tool, create_execution_capability, create_workflow_tool)
```

---

## Configuration

### .env additions:
```bash
# Tier 1: Docker
DOCKER_SECURITY_IMAGE=nexus-security-toolkit
DOCKER_NETWORK_MODE=host  # or "bridge" for isolation
DOCKER_WORKSPACE_MOUNT=true

# Tier 2: Device
NEXUS_DEVICES='[{"id":"pixel6a","type":"adb","address":"192.168.1.100:5555","rooted":true}]'

# Tier 3: Cloud
ALIBABA_ACCESS_KEY_ID=xxx
ALIBABA_ACCESS_KEY_SECRET=xxx
ALIBABA_REGION=cn-hangzhou
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx
```

### Agent prompt addition (Infrastructure Map):
```
## EXECUTION CAPABILITIES (Live)
- Tier 0 (In-Process): Python, file I/O, web search ✅
- Tier 1 (Docker): jadx, apktool, nuclei, frida-tools, nmap ✅
- Tier 2 (Device): Pixel 6a (rooted, Frida ready) ✅
- Tier 3 (Cloud): Alibaba Cloud (STS active) ✅
- Tier 4 (Self-Evolve): Can create any tool on demand ✅

Use the highest appropriate tier. If a tier is unavailable,
either bootstrap it or fall back to producing user-runnable scripts.
```

---

## ReelShort Example (how the task SHOULD have gone):

```
Turn 1:
  Agent sees: Tier 1 AVAILABLE (Docker + jadx)
  Agent calls: docker_exec("wget https://apkpure.com/.../com.newleaf.app.android.victor.apk -O /workspace/reelshort.apk")
  Agent calls: docker_exec("jadx -d /workspace/jadx_output /workspace/reelshort.apk")
  Agent calls: read_file("/workspace/jadx_output/AndroidManifest.xml")
  → Real decompilation, real findings, turn 1!

Turn 2:
  Agent sees: Tier 2 AVAILABLE (Pixel 6a)
  Agent calls: device_push("_work/frida_master_reelshort.js", "/data/local/tmp/hook.js")
  Agent calls: frida_run(script="_work/frida_master_reelshort.js", package="com.newleaf.app.android.victor")
  → Real Frida output, real token capture, turn 2!

Turn 3:
  Agent has auth token from Frida
  Agent calls: docker_exec("python3 api_fuzzer.py --token {captured_token} --target api.reelshort.com")
  → Real API fuzzing, real vulnerability discovery, turn 3!

Total: 3-5 turns instead of 17.
```

---

## Implementation Priority:

1. **docker_exec tool** — biggest bang for buck, enables 80% of missing capabilities
2. **Infrastructure Map update** — reflect Docker availability in agent prompt
3. **create_execution_capability** — agents self-extend via Docker wrappers
4. **device_exec / frida_run** — mobile-specific testing (needs physical device)
5. **cloud_exec** — cloud security testing

---

*Architecture Plan — Ready for Implementation*
