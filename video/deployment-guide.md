# OpenMCP Gateway — Linux VM Deployment Guide

## Overview

This guide takes you from a bare Linux VM to a fully running OpenMCP Gateway that wraps your own `server.py` MCP server behind a single authenticated HTTP endpoint.

```
┌─────────────────────────────────────────────────────────────────┐
│  CLIENT SIDE                                                    │
│  Any MCP client (Claude Desktop, Python SDK, your app…)        │
│  sends requests to  http://<VM-IP>:8080/mcp                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │  Streamable HTTP  (Bearer token)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  OPENMCP GATEWAY  (Docker container: openmcp-gateway)           │
│  • validates Bearer token                                       │
│  • routes tool calls → your server  (namespaced: myserver__foo) │
│  • supervises process, restarts on crash                        │
│  • exposes /health  /metrics  /admin                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │  stdio subprocess
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  YOUR MCP SERVER  (server.py inside the same container)         │
│  • reads tools / handles tool calls                             │
│  • loads secrets from .env via environment variables            │
│  • returns JSON results up the stdio pipe                       │
└─────────────────────────────────────────────────────────────────┘
```

The gateway and your server run **in the same Docker container** (simplest path). The gateway launches `server.py` as a stdio subprocess — no extra networking needed between them. The only port exposed to the network is `8080`.

---

## Prerequisites

| Requirement | Minimum | Notes |
| --- | --- | --- |
| Linux distro | Ubuntu 22.04 / Debian 12 / RHEL 9 | Any systemd-based distro works |
| Python | 3.11+ | Only needed for non-Docker path |
| Docker Engine | 24+ | `curl -fsSL https://get.docker.com | sh` |
| Docker Compose | v2 (plugin) | Included in Docker Engine 24+ |
| Open port | 8080 (TCP inbound) | Or any port you map in compose |
| Git | any | To clone the repo |

**Check your VM:**

```bash
python3 --version        # must be 3.11+
docker --version         # must be 24+
docker compose version   # must be v2.x
ss -tlnp | grep 8080     # should be empty (port free)
```

---

## Folder Structure (Single Server)

```
mcp-deploy/
├── server.py              ← your MCP server
├── .env                   ← your secrets (API keys, DB URLs)
├── requirements.txt       ← Python deps for server.py
├── Dockerfile
├── docker-compose.yml
└── config/
    └── openmcp.yaml
```

---

## Folder Structure (5 Servers)

```
mcp-deploy/
├── .env                        ← shared secrets for all servers
├── Dockerfile
├── docker-compose.yml
├── config/
│   └── openmcp.yaml
└── servers/
    ├── billing/
    │   ├── server.py
    │   └── requirements.txt
    ├── search/
    │   ├── server.py
    │   └── requirements.txt
    ├── docs/
    │   ├── server.py
    │   └── requirements.txt
    ├── calendar/
    │   ├── server.py
    │   └── requirements.txt
    └── notifications/
        ├── server.py
        └── requirements.txt
```

---

## config/openmcp.yaml

```yaml
gateway:
  host: 0.0.0.0
  port: 8080
  allowed_hosts:
    - "localhost:8080"
    - "127.0.0.1:8080"
    - "<YOUR_VM_IP>:8080"
  allowed_origins:
    - "http://localhost:8080"
    - "http://<YOUR_VM_IP>:8080"

auth:
  mode: bearer
  tokens:
    - name: my-client
      # Generate: python3 -c "import hashlib; print(hashlib.sha256(b'mysecrettoken').hexdigest())"
      sha256: "REPLACE_WITH_SHA256_OF_YOUR_TOKEN"
      servers: ["*"]

logging:
  level: info
  format: json

storage:
  backend: sqlite
  path: /var/lib/openmcp/registry.db

# Single server example:
servers:
  - id: myserver
    description: "My MCP server"
    command: python3
    args: ["/app/server.py"]

# Multi-server example (5 servers):
# servers:
#   - id: billing
#     command: python3
#     args: ["/app/servers/billing/server.py"]
#   - id: search
#     command: python3
#     args: ["/app/servers/search/server.py"]
#   - id: docs
#     command: python3
#     args: ["/app/servers/docs/server.py"]
#   - id: calendar
#     command: python3
#     args: ["/app/servers/calendar/server.py"]
#   - id: notifications
#     command: python3
#     args: ["/app/servers/notifications/server.py"]
```

**Generate your token hash:**

```bash
python3 -c "import hashlib; print(hashlib.sha256(b'mysecrettoken').hexdigest())"
# Clients send:  Authorization: Bearer mysecrettoken
```

Tool names are automatically namespaced:

| Tool in server.py | What the client calls |
| --- | --- |
| `create_invoice` (billing) | `billing__create_invoice` |
| `search_docs` (search) | `search__search_docs` |
| `get_page` (docs) | `docs__get_page` |
| `add_event` (calendar) | `calendar__add_event` |
| `send_alert` (notifications) | `notifications__send_alert` |

---

## Dockerfile

```dockerfile
FROM python:3.12-slim AS build
WORKDIR /build
COPY --from=local /opt/mcp-bridge /build/mcp-bridge
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir /build/mcp-bridge

FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    OPENMCP_CONFIG=/etc/openmcp/openmcp.yaml

WORKDIR /app

# Single server:
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# Multi-server (replace above with):
# COPY servers/billing/requirements.txt  ./billing-req.txt
# COPY servers/search/requirements.txt   ./search-req.txt
# COPY servers/docs/requirements.txt     ./docs-req.txt
# COPY servers/calendar/requirements.txt ./calendar-req.txt
# COPY servers/notifications/requirements.txt ./notifications-req.txt
# RUN pip install --no-cache-dir \
#       -r billing-req.txt -r search-req.txt -r docs-req.txt \
#       -r calendar-req.txt -r notifications-req.txt
# COPY servers/ /app/servers/

RUN groupadd --system --gid 10001 openmcp \
 && useradd --system --uid 10001 --gid openmcp \
      --home-dir /var/lib/openmcp --create-home openmcp
VOLUME ["/var/lib/openmcp"]
USER openmcp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"
ENTRYPOINT ["openmcp"]
CMD ["serve"]
```

---

## docker-compose.yml

```yaml
services:
  gateway:
    build:
      context: .
    image: my-mcp-gateway:latest
    ports:
      - "0.0.0.0:8080:8080"
    volumes:
      - ./config:/etc/openmcp:ro
      - openmcp-data:/var/lib/openmcp
    env_file:
      - .env
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s   # increase to 40s for 5 servers

volumes:
  openmcp-data:
```

---

## Step-by-Step Deploy

```bash
# 1. Clone the gateway repo (one-time)
sudo git clone https://github.com/DhaneshGore/mcp-bridge.git /opt/mcp-bridge

# 2. Create deployment folder
mkdir ~/mcp-deploy && cd ~/mcp-deploy

# 3. Copy your files
cp /path/to/server.py .
cp /path/to/.env .
cp /path/to/requirements.txt .
mkdir config

# 4. Create config/openmcp.yaml, Dockerfile, docker-compose.yml
#    (use the templates above)

# 5. Generate bearer token hash
python3 -c "import hashlib; print(hashlib.sha256(b'YOUR_SECRET_TOKEN').hexdigest())"
# Paste output into config/openmcp.yaml → auth.tokens[0].sha256

# 6. Find your VM IP and update config
hostname -I | awk '{print $1}'
# Edit config/openmcp.yaml and replace <YOUR_VM_IP>

# 7. Build and start
docker compose build
docker compose up -d

# 8. Verify
docker compose ps
curl http://localhost:8080/health   # → {"status":"ok"}
curl http://localhost:8080/ready    # → {"status":"ready"}

# 9. Restart after changes
docker compose down
docker compose build --no-cache
docker compose up -d
```

---

## How It Works End-to-End

```
1. CLIENT sends HTTP POST to http://<VM-IP>:8080/mcp
   Headers: Authorization: Bearer YOUR_SECRET_TOKEN

2. GATEWAY checks token → routes to correct server by namespace prefix

3. GATEWAY pipes call to server.py via stdin

4. server.py runs your logic (API call, DB query, etc.)

5. server.py writes result to stdout

6. GATEWAY returns HTTP response to client
```

**Timeouts explained:**

| Timeout | When it fires | Server process affected? |
| --- | --- | --- |
| `startup: 30` | Only at boot — waiting for server.py to initialize | No — retried with backoff |
| `request: 60` | One call takes too long — that call is cancelled | No — process stays alive |
| `shutdown: 5` | `docker compose down` — grace period to exit cleanly | Yes — then force-killed |

The server process runs **continuously**. No calls = process still running, port still open.

---

## Verification & Troubleshooting

```bash
curl http://localhost:8080/health     # {"status":"ok"}
curl http://localhost:8080/ready      # {"status":"ready"}
curl http://localhost:8080/version    # gateway version
docker compose logs -f                # live logs

# Run built-in doctor
docker compose exec gateway openmcp --config /etc/openmcp/openmcp.yaml doctor

# Check env vars loaded from .env
docker compose exec gateway env | grep MY_API_KEY
```

**Common errors:**

| Error | Cause | Fix |
| --- | --- | --- |
| `401 Unauthorized` | Wrong token | Check `Authorization: Bearer <token>` header |
| `400 Bad Request: invalid host` | VM IP not in allowed_hosts | Add IP to config |
| `/ready` returns `starting` | server.py failed to start | Check `docker compose logs` for traceback |
| `ModuleNotFoundError` | Missing dep | Add to `requirements.txt` and rebuild |
| `KeyError: MY_API_KEY` | .env not loaded | Check `.env` exists, key names match |
| Port 8080 in use | Another process | `ss -tlnp | grep 8080` to find it |
