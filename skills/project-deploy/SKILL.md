# Skill: Project Deploy (Local)

Deploy projects built by agents to a local dev server and deliver real URLs to users.

## When to Use

- User requests: "deploy", "demo URL", "preview", "chạy thử", "run", "tạo URL cho tôi"
- After building a project (website, app, API) that needs to be accessible

## Prerequisites

- Project code exists in workspace (written via write_file/edit_file)
- Dependencies installed (npm install / pip install)

## Deploy Procedure

### Step 1: Identify Technology

Detect the framework from the project:
| Project has | Tech value |
|---|---|
| `package.json` with vite/react/vue/svelte | `vite` |
| `package.json` with next | `next` |
| `requirements.txt` with fastapi/uvicorn | `fastapi` |
| `requirements.txt` with flask | `flask` |
| `package.json` with express | `express` |
| `manage.py` (Django) | `django` |

### Step 2: Start Dev Server

```
Tool: start_dev_server
Params:
  project_path: "<relative path in workspace>"
  tech: "vite" | "next" | "fastapi" | "flask" | "express" | "django"
  port: 0  (auto-allocate, or specify if needed)
```

The server will:
- Bind to `0.0.0.0` (accessible from outside, not just localhost)
- Auto-allocate port: frontend 3000-3999, backend 8001-8999
- Auto-detect external IP via UDP socket trick
- Attempt to open firewall port (ufw/iptables) if configured

### Step 3: Get the URL

From the tool response, extract:
- `external_url` — This is what you give to the user (IP:port format)
- `internal_url` — Only for health checks (localhost)
- `server_id` — For managing the server later

### Step 4: Verify Server is Running

```
Tool: browser_navigate
Params:
  url: "<external_url from step 3>"
```

If it fails, debug:
```
Tool: get_server_logs
Params:
  server_id: "<server_id from step 3>"
  lines: 50
```

Fix issues and restart if needed.

### Step 5: (Optional) Expose via Tunnel

If the user needs access from the internet (not just LAN), or if running inside Docker/NAT:

```
Tool: expose_port
Params:
  port: <port from step 3>
```

This tries cloudflared → bore → SSH tunnel and returns a public URL.

### Step 6: Deliver URL to User

Report the REAL URL to user:
- Prefer `external_url` from start_dev_server (LAN accessible)
- Or tunnel URL from expose_port (internet accessible)

## CRITICAL RULES

1. **NEVER fabricate URLs** — Only URLs returned by tools are valid
2. **NEVER give localhost to user** — They cannot access it from their browser
3. **NEVER skip deploy** when user asked for a live URL/demo
4. **ALWAYS verify** the server is actually running before reporting URL
5. **If server fails**: read logs → fix code → restart → verify again

## Host Binding Reference

All frameworks are pre-configured to bind `0.0.0.0`:

| Tech | Command |
|---|---|
| vite | `npx vite --port PORT --host` |
| next | `npx next dev --port PORT --hostname 0.0.0.0` |
| fastapi | `uvicorn app.main:app --host 0.0.0.0 --port PORT` |
| flask | `flask run --host 0.0.0.0 --port PORT` |
| express | `node server.js` (ensure code listens on 0.0.0.0) |
| django | `python manage.py runserver 0.0.0.0:PORT` |
| webpack | `npx webpack serve --port PORT --host 0.0.0.0` |

## Configuration (.env)

```
# Explicit external URL base (empty = auto-detect IP)
EXTERNAL_BASE_URL=

# Auto-open firewall port on server start
AUTO_OPEN_FIREWALL_PORT=true

# Port ranges
PROJECT_BUILDER_FRONTEND_PORT_START=3100
PROJECT_BUILDER_FRONTEND_PORT_END=3199
PROJECT_BUILDER_BACKEND_PORT_START=8100
PROJECT_BUILDER_BACKEND_PORT_END=8199
```

## Error Recovery

| Issue | Action |
|---|---|
| Port already in use | Set port=0 for auto-allocation |
| Server timeout | Check get_server_logs, fix startup errors |
| Permission denied | Check firewall, port range |
| Module not found | Re-run dependency install |
| External URL = localhost | Use expose_port for tunnel |

## Example Conversation

User: "Tạo website dạy tiếng Anh và deploy cho tôi URL"

Agent flow:
1. Build project (write files, install deps)
2. `start_dev_server(project_path="english-learning", tech="vite")`
3. Response: `external_url = "http://192.168.1.100:3100"`
4. `browser_navigate(url="http://192.168.1.100:3100")` — verify loads
5. Report to user: "Website đã deploy tại http://192.168.1.100:3100"

If user needs public internet access:
6. `expose_port(port=3100)`
7. Response: `url = "https://abc-xyz.trycloudflare.com"`
8. Report to user: "URL public: https://abc-xyz.trycloudflare.com"
