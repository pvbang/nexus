"""Setup script for stealth browser components.

Run this after installing dependencies:
    python scripts/setup_stealth_browser.py
"""

import subprocess
import sys


def run(cmd: list[str], check: bool = True) -> None:
    print(f"  > {' '.join(cmd)}")
    subprocess.run(cmd, check=check)


def main():
    print("=" * 60)
    print("  Stealth Browser Setup - MekongAI Nexus")
    print("=" * 60)

    # 1. Install Patchright browser binaries
    print("\n[1/3] Installing Patchright (undetected Chromium)...")
    try:
        run([sys.executable, "-m", "patchright", "install", "chromium"])
        print("  OK: Patchright Chromium installed")
    except Exception as e:
        print(f"  WARN: Patchright install failed: {e}")
        print("  Try: pip install patchright && python -m patchright install chromium")

    # 2. Install Camoufox browser binary
    print("\n[2/3] Installing Camoufox (anti-detect Firefox)...")
    try:
        run([sys.executable, "-m", "camoufox", "fetch"])
        print("  OK: Camoufox Firefox installed")
    except Exception as e:
        print(f"  WARN: Camoufox install failed: {e}")
        print("  Try: pip install camoufox && python -m camoufox fetch")

    # 3. Verify curl_cffi
    print("\n[3/3] Verifying curl_cffi (TLS fingerprint)...")
    try:
        import curl_cffi
        print(f"  OK: curl_cffi v{curl_cffi.__version__} available")
    except ImportError:
        print("  WARN: curl_cffi not installed")
        print("  Try: pip install curl_cffi")

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print()
    print("  Environment variables (optional):")
    print("    BROWSER_USE_STEALTH=true     Enable stealth (default: true)")
    print("    STEALTH_ENGINE=auto          auto|patchright|camoufox")
    print("    BROWSER_USE_HEADLESS=true    Run headless (default: true)")
    print("    BYPARR_URL=http://localhost:8191  Byparr service for CF bypass")
    print("    STEALTH_PROXY_URL=http://...      Explicit proxy")
    print()
    print("  Docker (Byparr for Cloudflare):")
    print("    docker run -d -p 8191:8191 thephaseless/byparr:latest")
    print("=" * 60)


if __name__ == "__main__":
    main()
