FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/jadx/bin:/usr/local/go/bin:/root/go/bin:${PATH}"

# Core packages
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    python3 python3-pip python3-venv \
    wget curl git unzip jq \
    nmap masscan netcat-openbsd socat dnsutils whois \
    tcpdump tshark \
    && rm -rf /var/lib/apt/lists/*

# Go (for nuclei, ffuf, etc)
RUN wget -q https://go.dev/dl/go1.22.4.linux-amd64.tar.gz -O /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz

# Android RE tools
RUN wget -q -O /tmp/jadx.zip https://github.com/skylot/jadx/releases/download/v1.5.1/jadx-1.5.1.zip \
    && mkdir -p /opt/jadx && unzip -q /tmp/jadx.zip -d /opt/jadx && rm /tmp/jadx.zip \
    && chmod +x /opt/jadx/bin/*
RUN wget -q -O /usr/local/bin/apktool.jar https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar \
    && printf '#!/bin/bash\njava -jar /usr/local/bin/apktool.jar "$@"\n' > /usr/local/bin/apktool \
    && chmod +x /usr/local/bin/apktool

# Go security tools
RUN go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
    && go install github.com/ffuf/ffuf/v2@latest \
    && go install github.com/projectdiscovery/httpx/cmd/httpx@latest \
    && go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest

# Python security tools
RUN pip3 install --no-cache-dir \
    frida-tools objection \
    mitmproxy \
    sqlmap \
    pwntools \
    impacket \
    androguard \
    requests httpx aiohttp

WORKDIR /workspace
CMD ["bash"]
