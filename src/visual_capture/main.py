"""Command line entry point for the screenshot downloader."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from .capture import MOUNT_JS, READY_JS, VIEWPORTS, capture_route, filename_token
from .routes import Route, list_routes, load_routes_file, resolve_uri, select_routes

TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT = TOOL_ROOT.parent / "laravel_booking"
DEFAULT_OUTPUT = TOOL_ROOT.parent / "visual-check-screenshots"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download full-page screenshots of any set of URLs, or of every Laravel GET route."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Site origin used for relative routes")
    parser.add_argument(
        "--output",
        type=Path,
        help="Artifact directory (default: ../visual-check-screenshots, next to this tool)",
    )
    parser.add_argument(
        "--routes-file",
        type=Path,
        help="JSON list of relative paths, e.g. ['/', '/pricing'] or [{\"uri\": \"/docs\"}]",
    )
    parser.add_argument("--urls-file", type=Path, help="Text file with one absolute URL per line")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="Laravel root for automatic route discovery")
    parser.add_argument("--email", help="Login email")
    parser.add_argument("--password", help="Login password")
    parser.add_argument("--login-url", default="/login", help="Login path (relative to --base-url)")
    parser.add_argument("--email-selector", default="#email", help="CSS selector for the email field")
    parser.add_argument("--password-selector", default="#password", help="CSS selector for the password field")
    parser.add_argument("--submit-selector", default="button[type=submit]", help="CSS selector for the submit button")
    parser.add_argument("--success-url", default="**/dashboard", help="Glob waited for after login")
    parser.add_argument(
        "--ready-js",
        help="JS predicate that resolves true when the page is ready "
        "(default: generic document.readyState; Laravel auto-discovery uses the Inertia mount predicate)",
    )
    parser.add_argument(
        "--viewports",
        default=",".join(VIEWPORTS),
        help="Comma-separated subset of: " + ", ".join(VIEWPORTS),
    )
    parser.add_argument("--only", help="Regex: include only matching route paths")
    parser.add_argument("--skip", help="Regex: exclude matching route paths")
    parser.add_argument("--timeout", type=int, default=90_000, help="Per-navigation timeout in ms")
    parser.add_argument("--settle-ms", type=int, default=800, help="Wait after load/fonts before screenshot")
    return parser.parse_args(argv)


def load_targets(args: argparse.Namespace) -> list[Route]:
    """Choose the route source: explicit files win over Laravel discovery."""
    if args.routes_file:
        return load_routes_file(args.routes_file)
    if args.urls_file:
        urls = [line.strip() for line in args.urls_file.read_text(encoding="utf-8").splitlines()]
        return [Route(uri=url, name=url) for url in urls if url and not url.startswith("#")]
    project = args.project.resolve()
    if not (project / "artisan").is_file():
        raise RuntimeError(f"No artisan file in {project}; pass --routes-file or --urls-file")
    return list_routes(project)


def discover_values(page, base_url: str, timeout: int, ready_js: str) -> dict[str, str]:
    """Find a real service slug so `{service}` routes can be rendered in Laravel."""
    values: dict[str, str] = {}
    page.goto(base_url + "/services", wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_function(ready_js, timeout=timeout)
    slug = page.evaluate(
        """() => {
            const a = [...document.querySelectorAll('a[href]')]
                .map((x) => (x.getAttribute('href') || '').match(/\\/services\\/([^/?#]+)/))
                .find(Boolean);
            return a ? a[1] : null;
        }"""
    )
    if slug:
        values["service"] = slug
    return values


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        routes = load_targets(args)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    viewports = [v.strip() for v in args.viewports.split(",") if v.strip()]
    unknown = [v for v in viewports if v not in VIEWPORTS]
    if unknown:
        print(f"ERROR: unknown viewports: {', '.join(unknown)}", file=sys.stderr)
        return 2

    laravel_mode = not args.routes_file and not args.urls_file
    ready_js = args.ready_js or (MOUNT_JS if laravel_mode else READY_JS)
    output = (args.output or DEFAULT_OUTPUT).resolve()
    output.mkdir(parents=True, exist_ok=True)
    viewport_dirs = {v: output / v for v in viewports}
    for directory in viewport_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)

    captures: list[dict] = []
    values: dict[str, str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": VIEWPORTS[viewports[0]][0], "height": VIEWPORTS[viewports[0]][1]})
        page.set_default_navigation_timeout(args.timeout)

        try:
            if bool(args.email) != bool(args.password):
                print("ERROR: pass both --email and --password, or neither", file=sys.stderr)
                return 2
            if args.email:
                page.goto(args.base_url + args.login_url, wait_until="domcontentloaded", timeout=args.timeout)
                page.locator(args.email_selector).fill(args.email)
                page.locator(args.password_selector).fill(args.password)
                page.locator(args.submit_selector).click()
                page.wait_for_url(args.success_url, timeout=args.timeout)

            if laravel_mode:
                values = discover_values(page, args.base_url, args.timeout, ready_js)
            selected, skipped = select_routes(routes, values, args.only, args.skip)

            for route in selected:
                is_absolute = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", route.uri)
                target = route.uri if is_absolute else args.base_url + resolve_uri(route, values)
                for viewport in viewports:
                    result = capture_route(
                        page, target, viewport, viewport_dirs[viewport], args.timeout,
                        ready_js, args.settle_ms, filename_token(route.uri),
                    )
                    captures.append(asdict(result))
                    state = result.error or f"status={result.status or '---'}"
                    print(f"{viewport:<16} {target:<48} {state}")
        finally:
            browser.close()

    failures = [c for c in captures if c["error"] or not c["screenshot"]]
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "viewports": viewports,
        "sample_values": values,
        "routes_rendered": len(selected),
        "routes_skipped": [asdict(s) for s in skipped],
        "files_downloaded": len(captures) - len(failures),
        "failures": len(failures),
        "captures": captures,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_manifest_markdown(output / "manifest.md", manifest)

    print(f"\nManifest: {output / 'manifest.md'}")
    print(f"Downloaded: {manifest['files_downloaded']} PNGs · Failed: {manifest['failures']}")
    return 1 if failures else 0


def write_manifest_markdown(path: Path, manifest: dict) -> None:
    lines = [
        "# Screenshot download manifest",
        "",
        f"Generated: {manifest['generated_at']} · Base: {manifest['base_url']}",
        "",
        "| Route | Viewport | Status | File |",
        "| --- | --- | --- | --- |",
    ]
    for c in manifest["captures"]:
        file = f"`{c['screenshot']}`" if c["screenshot"] else "—"
        status = c["error"] or str(c["status"])
        lines.append(f"| `{c['route']}` | {c['viewport']} | {status} | {file} |")
    if manifest["routes_skipped"]:
        lines += ["", "## Skipped routes", ""]
        lines += [f"- `{s['uri']}` — {s['reason']}" for s in manifest["routes_skipped"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
