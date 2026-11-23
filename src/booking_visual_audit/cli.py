"""Command line entry point for the reusable image downloader."""

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
        description="Download screenshots of any set of URLs, or of every Laravel GET route."
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
    parser.add_argument("--download-only", action="store_true", help="Just save PNGs, skip pixel/color analysis")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on errors, overflow or low brand color")
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

    console_errors: list[str] = []
    page_errors: list[str] = []
    captures: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": VIEWPORTS[viewports[0]][0], "height": VIEWPORTS[viewports[0]][1]})
        page.set_default_navigation_timeout(args.timeout)
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

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

            values = {}
            if laravel_mode:
                values = discover_values(page, args.base_url, args.timeout, ready_js)
            selected, skipped = select_routes(routes, values, args.only, args.skip)

            for route in selected:
                is_absolute = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", route.uri)
                target = route.uri if is_absolute else args.base_url + resolve_uri(route, values)
                for viewport in viewports:
                    result = capture_route(
                        page, target, viewport, viewport_dirs[viewport], args.timeout,
                        console_errors, page_errors, ready_js, args.download_only,
                        args.settle_ms, filename_token(route.uri),
                    )
                    captures.append(asdict(result))
                    print(
                        f"{result.status or '---'} {viewport:<16} {target:<48} "
                        f"brand={result.brand_ratio if not args.download_only else 'skip'} overflow={result.overflow_px}"
                    )
        finally:
            browser.close()

    # Compare each viewport against the home page at that same viewport; a single
    # global baseline makes narrow screens look falsely worse than wide ones.
    baselines: dict[str, float] = {}
    if not args.download_only:
        home = args.base_url.rstrip("/") + "/"
        baselines = {c["viewport"]: c["brand_ratio"] for c in captures if c["route"] == home}
    for capture in captures:
        reference = baselines.get(capture["viewport"])
        threshold = reference * 0.30 if reference else None
        capture["low_brand"] = bool(threshold and capture["brand_ratio"] < threshold)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "mode": "download" if args.download_only else "audit",
        "viewports": viewports,
        "sample_values": values,
        "baseline_brand_ratio": baselines or None,
        "low_brand_threshold": {v: round(b * 0.30, 4) for v, b in baselines.items()},
        "routes_rendered": len(selected),
        "routes_skipped": [asdict(s) for s in skipped],
        "captures": captures,
        "issues": {
            "console_errors": sum(len(c["console_errors"]) for c in captures),
            "page_errors": sum(len(c["page_errors"]) for c in captures),
            "overflow": sum(1 for c in captures if c["overflow_px"] > 0),
            "low_brand": sum(1 for c in captures if c["low_brand"]),
        },
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(output / "report.md", report)

    issues = report["issues"]
    print(f"\nReport: {output / 'report.md'}")
    print(
        f"Issues: console={issues['console_errors']} page={issues['page_errors']} "
        f"overflow={issues['overflow']} low-brand={issues['low_brand']}"
    )
    return 1 if args.strict and any(issues.values()) else 0


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# Image download report",
        "",
        f"Generated: {report['generated_at']} · Mode: {report['mode']} · Base: {report['base_url']}",
        "",
        "| Route | Viewport | Status | Overflow | Brand ratio | Flags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in report["captures"]:
        flags = []
        if c["console_errors"]:
            flags.append("console")
        if c["page_errors"]:
            flags.append("pageerror")
        if c["overflow_px"]:
            flags.append("overflow")
        if c["low_brand"]:
            flags.append("low-brand")
        ratio = "skip" if report["mode"] == "download" else f"{c['brand_ratio']:.3f}"
        lines.append(
            f"| `{c['route']}` | {c['viewport']} | {c['status']} | {c['overflow_px']} | "
            f"{ratio} | {', '.join(flags) or 'ok'} |"
        )
    if report["routes_skipped"]:
        lines += ["", "## Skipped routes", ""]
        lines += [f"- `{s['uri']}` — {s['reason']}" for s in report["routes_skipped"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
