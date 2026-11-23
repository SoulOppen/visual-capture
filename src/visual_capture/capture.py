"""Browser screenshot capture and download."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    error: str | None = None


def capture_route(
    page: Page,
    target: str,
    viewport_name: str,
    output: Path,
    timeout: int,
    ready_js: str = MOUNT_JS,
    settle_ms: int = 800,
    label: str | None = None,
) -> CaptureResult:
    width, height = VIEWPORTS[viewport_name]
    page.set_viewport_size({"width": width, "height": height})

    response = None
    for attempt in range(2):
        try:
            response = page.goto(target, wait_until="load", timeout=timeout)
            break
        except PlaywrightError as exc:
            if attempt:
                return CaptureResult(
                    route=target,
                    viewport=viewport_name,
                    screenshot="",
                    status=None,
                    title=None,
                    h1=None,
                    error=f"Navigation failed: {exc}",
                )
            page.wait_for_timeout(1000)

    status = response.status if response else None
    error = None
    try:
        page.wait_for_function(ready_js, timeout=timeout)
        page.evaluate("() => document.fonts?.ready")
    except PlaywrightError as exc:
        error = f"Ready predicate failed: {exc}"
    page.wait_for_timeout(settle_ms)
    info = page.evaluate(
        """() => ({
            title: document.title,
            h1: document.querySelector('h1')?.textContent?.trim() ?? null,
        })"""
    )

    path = output / f"{viewport_name}__{label or filename_token(target)}.png"
    _save_screenshot(page, path)
    return CaptureResult(
        route=target,
        viewport=viewport_name,
        screenshot=f"{viewport_name}/{path.name}",
        status=status,
        title=info.get("title"),
        h1=info.get("h1"),
        error=error,
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
