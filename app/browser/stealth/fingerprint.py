"""Browser fingerprint generation for realistic browser profiles."""

import random
from dataclasses import dataclass, field


@dataclass
class BrowserFingerprint:
    user_agent: str = ""
    viewport_width: int = 1920
    viewport_height: int = 1080
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    pixel_ratio: float = 1.0
    timezone: str = "Asia/Ho_Chi_Minh"
    locale: str = "en-US"
    platform: str = "Win32"
    hardware_concurrency: int = 8
    device_memory: int = 8
    webgl_vendor: str = "Google Inc. (NVIDIA)"
    webgl_renderer: str = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"
    extra_headers: dict = field(default_factory=dict)


# Real-world browser profiles sampled from actual traffic distributions
_CHROME_VERSIONS = [
    "131.0.6778.85", "131.0.6778.109", "132.0.6834.57", "132.0.6834.83",
    "133.0.6876.0", "133.0.6876.46", "134.0.6915.0", "134.0.6915.45",
    "135.0.6960.0", "136.0.7001.0",
]

_FIREFOX_VERSIONS = [
    "133.0", "134.0", "135.0", "136.0", "137.0", "138.0",
]

_SCREEN_RESOLUTIONS = [
    (1920, 1080), (2560, 1440), (1366, 768), (1536, 864),
    (1440, 900), (1680, 1050), (3840, 2160), (2560, 1080),
]

_WEBGL_RENDERERS = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]

_PLATFORMS = ["Win32", "Win32", "Win32", "Linux x86_64", "MacIntel"]

_TIMEZONES = [
    "Asia/Ho_Chi_Minh", "America/New_York", "America/Los_Angeles",
    "Europe/London", "Europe/Paris", "Asia/Tokyo", "Asia/Singapore",
]

_LOCALES = ["en-US", "en-GB", "en-US", "en-US", "vi-VN"]


def generate_fingerprint(engine: str = "chrome") -> BrowserFingerprint:
    """Generate a realistic, internally-consistent browser fingerprint."""
    screen = random.choice(_SCREEN_RESOLUTIONS)
    webgl = random.choice(_WEBGL_RENDERERS)
    platform = random.choice(_PLATFORMS)

    pixel_ratio = 2.0 if screen[0] >= 2560 else 1.0
    hw_concurrency = random.choice([4, 8, 12, 16])
    device_memory = random.choice([4, 8, 16, 32])

    if engine == "firefox":
        version = random.choice(_FIREFOX_VERSIONS)
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}) Gecko/20100101 Firefox/{version}"
    else:
        version = random.choice(_CHROME_VERSIONS)
        ua = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{version} Safari/537.36"
        )

    return BrowserFingerprint(
        user_agent=ua,
        viewport_width=screen[0],
        viewport_height=screen[1],
        screen_width=screen[0],
        screen_height=screen[1],
        color_depth=24,
        pixel_ratio=pixel_ratio,
        timezone=random.choice(_TIMEZONES),
        locale=random.choice(_LOCALES),
        platform=platform,
        hardware_concurrency=hw_concurrency,
        device_memory=device_memory,
        webgl_vendor=webgl[0],
        webgl_renderer=webgl[1],
    )


def fingerprint_to_js_overrides(fp: BrowserFingerprint) -> str:
    """Generate JavaScript to inject fingerprint overrides into page context."""
    return f"""
    Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => {fp.hardware_concurrency}}});
    Object.defineProperty(navigator, 'deviceMemory', {{get: () => {fp.device_memory}}});
    Object.defineProperty(navigator, 'platform', {{get: () => '{fp.platform}'}});
    Object.defineProperty(screen, 'width', {{get: () => {fp.screen_width}}});
    Object.defineProperty(screen, 'height', {{get: () => {fp.screen_height}}});
    Object.defineProperty(screen, 'colorDepth', {{get: () => {fp.color_depth}}});
    Object.defineProperty(window, 'devicePixelRatio', {{get: () => {fp.pixel_ratio}}});

    // WebGL spoofing
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {{
        if (parameter === 37445) return '{fp.webgl_vendor}';
        if (parameter === 37446) return '{fp.webgl_renderer}';
        return getParameter.call(this, parameter);
    }};
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {{
        if (parameter === 37445) return '{fp.webgl_vendor}';
        if (parameter === 37446) return '{fp.webgl_renderer}';
        return getParameter2.call(this, parameter);
    }};

    // Timezone spoofing
    const originalDateTimeFormat = Intl.DateTimeFormat;
    Intl.DateTimeFormat = function(...args) {{
        if (!args[1] || !args[1].timeZone) {{
            args[1] = args[1] || {{}};
            args[1].timeZone = '{fp.timezone}';
        }}
        return new originalDateTimeFormat(...args);
    }};
    Object.setPrototypeOf(Intl.DateTimeFormat, originalDateTimeFormat);
    """
