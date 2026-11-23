"""Browser capture and pixel-level palette analysis."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

VIEWPORTS = {
    "mobile": (375, 812),
    "tablet-portrait": (768, 1024),
    "tablet-landscape": (1024, 768),
    "desktop": (1440, 900),
}

# Generic predicate: works on any site, not just this Inertia/React app.
READY_JS = "() => document.readyState === 'complete'"
# Laravel starter-kit predicate: the React root has mounted its children.
MOUNT_JS = "() => (document.querySelector('#app')?.children.length ?? 0) > 0"


@dataclass
class CaptureResult:
    route: str
    viewport: str
    screenshot: str
    status: int | None
    title: str | None
    h1: str | None
    overflow_px: int
    console_errors: list[str]
    page_errors: list[str]
    brand_ratio: float
    top_colors: list[str]


def _is_brand_pixel(r: int, g: int, b: int) -> bool:
    """Heuristic: indigo/violet pixels have blue clearly above red and green."""
    return b > r + 8 and b > g + 8 and b > 110


def analyze_image(path: Path) -> tuple[float, list[str]]:
    image = Image.open(path).convert("RGB")
    image.thumbnail((480, 480))
    pixels = list(image.getdata())
    brand = sum(_is_brand_pixel(r, g, b) for r, g, b in pixels)
    palette = image.quantize(colors=6).getpalette()[:18]
    colors = [f"#{palette[i]:02x}{palette[i + 1]:02x}{palette[i + 2]:02x}" for i in range(0, 18, 3)]
    return round(brand / len(pixels), 4), colors


def login(page: Page, base_url: str, email: str, password: str, timeout: int) -> None:
    page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_function(MOUNT_JS, timeout=timeout)
    page.locator("#email").fill(email)
    page.locator("#password").fill(password)
    page.locator("button[type=submit]").click()
    page.wait_for_url("**/dashboard", timeout=timeout)


def capture_route(
    page: Page,
    target: str,
    viewport_name: str,
    output: Path,
    timeout: int,
    console_errors: list[str],
    page_errors: list[str],
    ready_js: str = MOUNT_JS,
    download_only: bool = False,
    settle_ms: int = 800,
    label: str | None = None,
) -> CaptureResult:
    width, height = VIEWPORTS[viewport_name]
    page.set_viewport_size({"width": width, "height": height})
    console_errors.clear()
    page_errors.clear()

    response = None
    for attempt in range(2):
        try:
            response = page.goto(target, wait_until="load", timeout=timeout)
            break
        except PlaywrightError as exc:
            if attempt:
                page_errors.append(f"Navigation failed: {exc}")
                return CaptureResult(
                    route=target,
                    viewport=viewport_name,
                    screenshot="",
                    status=None,
                    title=None,
                    h1=None,
                    overflow_px=0,
                    console_errors=list(console_errors),
                    page_errors=list(page_errors),
                    brand_ratio=0.0,
                    top_colors=[],
                )
            page.wait_for_timeout(1000)

    status = response.status if response else None
    try:
        page.wait_for_function(ready_js, timeout=timeout)
        page.evaluate("() => document.fonts?.ready")
    except PlaywrightError as exc:
        page_errors.append(f"Ready predicate failed: {exc}")
    page.wait_for_timeout(settle_ms)
    info = page.evaluate(
        """() => ({
            title: document.title,
            h1: document.querySelector('h1')?.textContent?.trim() ?? null,
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        })"""
    )

    path = output / f"{viewport_name}__{label or filename_token(target)}.png"
    _save_screenshot(page, path)
    brand_ratio, top_colors = analyze_image(path) if not download_only else (0.0, [])
    return CaptureResult(
        route=target,
        viewport=viewport_name,
        screenshot=f"{viewport_name}/{path.name}",
        status=status,
        title=info.get("title"),
        h1=info.get("h1"),
        overflow_px=int(info.get("overflow") or 0),
        console_errors=list(console_errors),
        page_errors=list(page_errors),
        brand_ratio=brand_ratio,
        top_colors=top_colors,
    )


def _save_screenshot(page: Page, path: Path) -> None:
    """Write to a temp file then rename; some network/Windows mounts reject direct overwrites."""
    for attempt in range(3):
        handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".shot-", suffix=".png", delete=False)
        tmp_path = Path(handle.name)
        handle.close()
        try:
            page.screenshot(path=str(tmp_path), full_page=True)
            os.replace(tmp_path, path)
            return
        except (OSError, PlaywrightError):
            tmp_path.unlink(missing_ok=True)
            if attempt == 2:
                raise
            page.wait_for_timeout(500)


def filename_token(route: str) -> str:
    token = route.strip("/").replace("/", "__")
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in token)[:80] or "root"

