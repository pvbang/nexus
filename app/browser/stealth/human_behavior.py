"""Human-like behavior simulation to defeat behavioral bot detection."""

import asyncio
import math
import random


def bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple, p3: tuple) -> tuple:
    """Calculate point on cubic Bezier curve at parameter t."""
    u = 1 - t
    return (
        u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
    )


def generate_human_mouse_path(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int = 20,
) -> list[tuple[int, int]]:
    """Generate a human-like mouse movement path using Bezier curves with jitter."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]

    cp1 = (
        start[0] + dx * random.uniform(0.2, 0.4) + random.randint(-50, 50),
        start[1] + dy * random.uniform(0.0, 0.3) + random.randint(-50, 50),
    )
    cp2 = (
        start[0] + dx * random.uniform(0.6, 0.8) + random.randint(-50, 50),
        start[1] + dy * random.uniform(0.7, 1.0) + random.randint(-50, 50),
    )

    path = []
    for i in range(steps + 1):
        t = i / steps
        # Ease-in-out timing
        t = t * t * (3 - 2 * t)
        point = bezier_point(t, start, cp1, cp2, end)
        # Add micro-jitter to simulate hand tremor
        jitter_x = random.gauss(0, 1.5)
        jitter_y = random.gauss(0, 1.5)
        path.append((int(point[0] + jitter_x), int(point[1] + jitter_y)))

    return path


async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    """Wait a random human-like duration."""
    delay = random.uniform(min_ms, max_ms) / 1000.0
    await asyncio.sleep(delay)


async def human_type_delay() -> None:
    """Delay between keystrokes like a real human typing."""
    # Average typing speed ~200ms per char with variance
    delay = random.gauss(0.15, 0.05)
    delay = max(0.05, min(0.4, delay))
    await asyncio.sleep(delay)


async def human_scroll(page, direction: str = "down", distance: int | None = None) -> None:
    """Perform human-like scroll with variable speed."""
    if distance is None:
        distance = random.randint(200, 600)
    if direction == "up":
        distance = -distance

    # Scroll in small increments with varying speed
    remaining = distance
    while abs(remaining) > 0:
        chunk = min(abs(remaining), random.randint(50, 150))
        if remaining < 0:
            chunk = -chunk
        await page.evaluate(f"window.scrollBy(0, {chunk})")
        remaining -= chunk
        await asyncio.sleep(random.uniform(0.02, 0.08))


def generate_typing_pattern(text: str) -> list[tuple[str, float]]:
    """Generate realistic typing pattern with variable delays and occasional corrections."""
    pattern = []
    for i, char in enumerate(text):
        # Slower at word boundaries
        if char == " ":
            delay = random.gauss(0.2, 0.06)
        # Faster for common sequences
        elif i > 0 and text[i - 1:i + 1] in ("th", "he", "in", "er", "an", "on", "en"):
            delay = random.gauss(0.1, 0.03)
        else:
            delay = random.gauss(0.15, 0.05)

        delay = max(0.04, min(0.5, delay))
        pattern.append((char, delay))

    return pattern


# JavaScript to inject for mouse movement interception
HUMAN_BEHAVIOR_INIT_JS = """
// Prevent detection of programmatic mouse events
const origDispatch = EventTarget.prototype.dispatchEvent;
EventTarget.prototype.dispatchEvent = function(event) {
    if (event instanceof MouseEvent && event.isTrusted === false) {
        Object.defineProperty(event, 'isTrusted', {get: () => true});
    }
    return origDispatch.call(this, event);
};
"""
