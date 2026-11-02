# ============================================================
# Nexus - All-in-One Dockerfile
# Backend (FastAPI) + Frontend (React) + IronClaw (Rust) trong 1 container
# ============================================================

# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Build OpenClaw (Node.js gateway)
# Requires Node >=22.14.0; Debian-slim for native build tools
FROM node:22-bookworm-slim AS openclaw-build
WORKDIR /openclaw
RUN apt-get update && apt-get install -y --no-install-recommends \
    git make g++ python3 \
    && rm -rf /var/lib/apt/lists/*
RUN corepack enable
# Copy dependency manifests + workspace structure for resolution
COPY src/open-source/openclaw/package.json src/open-source/openclaw/pnpm-lock.yaml src/open-source/openclaw/pnpm-workspace.yaml ./
COPY src/open-source/openclaw/.npmrc ./
COPY src/open-source/openclaw/openclaw.mjs ./
COPY src/open-source/openclaw/patches/ ./patches/
COPY src/open-source/openclaw/scripts/ ./scripts/
COPY src/open-source/openclaw/extensions/ ./extensions/
COPY src/open-source/openclaw/packages/ ./packages/
COPY src/open-source/openclaw/ui/package.json ./ui/package.json
# Install deps; --ignore-scripts skips node-llama-cpp native build (not needed for TS compile)
RUN NODE_OPTIONS=--max-old-space-size=2048 pnpm install --frozen-lockfile --ignore-scripts 2>/dev/null \
    || pnpm install --ignore-scripts
# Copy source for build
COPY src/open-source/openclaw/src/ ./src/
COPY src/open-source/openclaw/tsdown.config.ts src/open-source/openclaw/tsconfig*.json ./
# Build dist (non-fatal: if build fails, create placeholder so COPY works)
RUN pnpm run build 2>/dev/null || pnpm exec tsdown 2>/dev/null || \
    (mkdir -p dist && echo "console.log('OpenClaw dist not built')" > dist/index.js)

# Stage 3: Build IronClaw (Rust sandbox binary)
# Non-fatal: if build fails, a placeholder is created so COPY doesn't break
FROM rust:1.82-slim AS ironclaw-build
WORKDIR /ironclaw
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
# Copy IronClaw source
COPY src/open-source/ironclaw/ .
# Build release binary. If it fails, create a placeholder so Docker COPY won't break.
# The app checks binary validity at runtime and falls back to Docker sandbox.
RUN mkdir -p /ironclaw/target/release && \
    (cargo build --release --bin ironclaw 2>&1 || \
     echo "#!/bin/sh\necho 'IronClaw not compiled'" > /ironclaw/target/release/ironclaw) && \
    chmod +x /ironclaw/target/release/ironclaw

# Stage 4: Backend + serve frontend static + IronClaw binary + OpenClaw dist
FROM python:3.12-slim

WORKDIR /app

# System deps (nodejs: required by capitol-ai-grafana IMDS refresh script
# so the container can self-refresh AWS Bedrock session tokens at runtime).
# ffmpeg: powers the video tools (video_info/extract_frames/extract_audio).
# tesseract-ocr (+vie): local OCR engine for extract_text_from_image; the 'vie'
# pack adds Vietnamese so scanned VN documents transcribe accurately. Without
# these the tools self-gate (check_fn) and report honestly — installing them
# turns the capability on for real in production.
#
# pandoc + libpango/cairo/harfbuzz/gdk-pixbuf: power the unified document
# renderer (app/documents). pandoc converts Markdown → .docx via a styled
# reference.docx; WeasyPrint needs the Pango/Cairo stack to turn HTML+CSS into
# .pdf with embedded Vietnamese fonts. Without them generate_pdf/word_document
# self-gate (check_fn) and report honestly instead of emitting broken files.
# dnsutils (dig/nslookup/host) + whois + nmap + jq: the pentest/OSINT toolset
# (execute_command) invokes these CLIs directly. Missing them makes a red-team
# task hit "command not found" and stall or fabricate (tri-run audit 2026-07-02,
# F-C1: the DeepSeek run died trying CLI recon with none of these installed).
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    dnsutils \
    ffmpeg \
    git \
    iproute2 \
    jq \
    libcairo2 \
    libffi-dev \
    libgdk-pixbuf-2.0-0 \
    libharfbuzz0b \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    nmap \
    nodejs \
    pandoc \
    procps \
    tesseract-ocr \
    tesseract-ocr-vie \
    whois \
    && rm -rf /var/lib/apt/lists/*

# Pre-install cloudflared so agents can create a real public URL (trycloudflare,
# no account) for apps/websites they host — without downloading the binary at
# runtime (which fails silently in air-gapped/blocked networks). tunnel.py prefers
# shutil.which("cloudflared"), so placing it on PATH means zero runtime download.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) cf_arch="amd64" ;; \
        arm64) cf_arch="arm64" ;; \
        *) cf_arch="amd64" ;; \
    esac; \
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}" \
        -o /usr/local/bin/cloudflared; \
    chmod +x /usr/local/bin/cloudflared; \
    /usr/local/bin/cloudflared --version

# Install uv for fast dependency management
RUN pip install uv

# Copy project files & install dependencies (full extras for all features)
COPY pyproject.toml .
# MED-01 FIX: Remove -e (editable) flag for production build.
# Editable installs are for development only.
RUN uv pip install --system ".[full]"

# Install Playwright browsers (for browser tools)
RUN python -m playwright install --with-deps chromium

# Install Patchright (undetected Chromium) + Camoufox (anti-detect Firefox)
RUN pip install patchright camoufox curl_cffi && \
    python -m patchright install chromium && \
    python -m camoufox fetch

# Copy backend source
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY developer_profile.json .
COPY skills/ skills/

# Copy hermes-agent source (for in-process tool integration)
COPY src/open-source/hermes-agent/ /app/src/open-source/hermes-agent/

# Copy capitol-ai-grafana refresh script (Node, built-in modules only — no npm install).
# Enables runtime AWS Bedrock token self-refresh via app/core/aws_refresher.py.
# output/ is created writable so the script can persist fresh creds + logs.
COPY src/open-source/capitol-ai-grafana/package.json /app/src/open-source/capitol-ai-grafana/package.json
COPY src/open-source/capitol-ai-grafana/lib/ /app/src/open-source/capitol-ai-grafana/lib/
COPY src/open-source/capitol-ai-grafana/scripts/ /app/src/open-source/capitol-ai-grafana/scripts/
RUN mkdir -p /app/src/open-source/capitol-ai-grafana/output && \
    chmod 777 /app/src/open-source/capitol-ai-grafana/output

# Copy IronClaw binary from build stage
# If Rust build failed, this is a placeholder script — app detects and falls back to Docker sandbox
COPY --from=ironclaw-build /ironclaw/target/release/ironclaw /app/bin/ironclaw
RUN chmod +x /app/bin/ironclaw

# Copy OpenClaw dist from build stage
COPY --from=openclaw-build /openclaw/dist/ /app/src/open-source/openclaw/dist/

# Copy frontend build output
COPY --from=frontend-build /frontend/dist /app/static

# Create workspace_data directory for file generation (reports, docx, etc.)
RUN mkdir -p /app/workspace_data && chmod 777 /app/workspace_data

# Copy entrypoint
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose app port + public gateway port + dev server port ranges
EXPOSE 8000 18800 19000-19049

# Environment
ENV NEXUS_SERVE_FRONTEND=true
ENV STATIC_DIR=/app/static
ENV AUTO_SCALE=true
# Mark the runtime as containerized so dev-server/tunnel logic reliably detects
# Docker (previously only /.dockerenv was checked; this env is read in several
# places — dev_server, dev_server_tools, public_link_setup — but was never set).
ENV RUNNING_IN_DOCKER=1
ENV IRONCLAW_BINARY_PATH=/app/bin/ironclaw
ENV OPENCLAW_DIST_PATH=/app/src/open-source/openclaw/dist/index.js

# Docker: Set WORKSPACE_DIR explicitly to absolute path
ENV WORKSPACE_DIR=/app/workspace_data

# Create symlink so agents using /workspace path also find files
RUN ln -sf /app/workspace_data /workspace 2>/dev/null || true

# Run with entrypoint script
CMD ["/app/entrypoint.sh"]
