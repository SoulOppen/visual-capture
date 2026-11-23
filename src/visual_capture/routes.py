"""Discover renderable GET routes from the Laravel application."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Routes that cannot be rendered by a fresh browser session.
SKIP_URIS = {
    "storage/{path}",
    "up",
    "verify-email/{id}/{hash}",
    "reset-password/{token}",
}

SKIP_PREFIXES = (
    "_ignition/",
    "sanctum/",
    "telescope/",
    "vendor/",
)


@dataclass(frozen=True)
class Route:
    uri: str
    name: str

    @property
    def is_page(self) -> bool:
        return not self.uri.startswith("api/")


@dataclass(frozen=True)
class SkippedRoute:
    uri: str
    reason: str


def list_routes(project: Path) -> list[Route]:
    """Run `artisan route:list --json` and parse its output."""
    result = subprocess.run(
        ["php", "artisan", "route:list", "--json"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"php artisan route:list failed in {project}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    start = result.stdout.find("[")
    if start < 0:
        raise RuntimeError(f"No JSON array in route:list output: {result.stdout[:200]!r}")
    raw = json.loads(result.stdout[start:])

    routes: list[Route] = []
    for entry in raw:
        methods = {m.strip().upper() for m in entry.get("method", "").split("|")}
        if "GET" not in methods and "HEAD" not in methods:
            continue
        uri = entry.get("uri", "").strip("/")
        routes.append(Route(uri=uri or "/", name=entry.get("name") or uri or "home"))
    return sorted(routes, key=lambda r: r.uri)


def load_routes_file(path: Path) -> list[Route]:
    """Load routes from a JSON file: a list of paths or objects with `uri`/`path`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("routes", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise RuntimeError(f"Expected a JSON list of routes in {path}")

    routes: list[Route] = []
    for entry in entries:
        if isinstance(entry, str):
            uri, name = entry, entry
        elif isinstance(entry, dict):
            uri = str(entry.get("uri") or entry.get("path") or "")
            name = str(entry.get("name") or uri)
        else:
            raise RuntimeError(f"Unsupported route entry in {path}: {entry!r}")
        routes.append(Route(uri=uri.strip("/") or "/", name=name))
    return sorted(routes, key=lambda r: r.uri)


def select_routes(
    routes: list[Route],
    values: dict[str, str],
    only: str | None = None,
    skip: str | None = None,
) -> tuple[list[Route], list[SkippedRoute]]:
    """Resolve path parameters and apply include/exclude filters."""
    selected: list[Route] = []
    skipped: list[SkippedRoute] = []
    only_re = re.compile(only) if only else None
    skip_re = re.compile(skip) if skip else None

    for route in routes:
        if skip_re and skip_re.search(route.uri):
            skipped.append(SkippedRoute(route.uri, "matched --skip"))
            continue
        if only_re and not only_re.search(route.uri):
            skipped.append(SkippedRoute(route.uri, "did not match --only"))
            continue
        if route.uri in SKIP_URIS or route.uri.startswith(SKIP_PREFIXES):
            skipped.append(SkippedRoute(route.uri, "non-renderable route"))
            continue

        missing = [p for p in re.findall(r"\{([^}]+)\}", route.uri) if p not in values]
        if missing:
            skipped.append(SkippedRoute(route.uri, f"no sample value for {', '.join(missing)}"))
            continue
        selected.append(route)
    return selected, skipped


def resolve_uri(route: Route, values: dict[str, str]) -> str:
    """Replace `{param}` placeholders with discovered sample values."""
    uri = route.uri
    for name, value in values.items():
        uri = uri.replace("{" + name + "}", value)
    return "/" + uri.lstrip("/")
