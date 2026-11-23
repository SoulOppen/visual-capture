# visual-capture

Capture and download full-page screenshots of any set of URLs at four viewports. Give it a list of URLs (or paths plus a base URL) and it renders each one in a real Chromium browser, saving one PNG per route and viewport. It has no dependency on Laravel or on any particular framework; route auto-discovery for Laravel is an optional convenience.

The tool captures and downloads images only. It does not score colors, detect overflow, or report visual defects.

## Setup

```bash
uv sync
uv run playwright install chromium
```

## Usage in any project

List the pages you want in a JSON file:

```json
["/", "/pricing", {"uri": "/docs", "name": "docs"}]
```

Then point the tool at it:

```bash
uv run visual-capture --routes-file ./pages.json --base-url https://example.com
```

Or use a plain text file with one absolute URL per line:

```bash
uv run visual-capture --urls-file ./urls.txt
```

By default the PNGs and the `manifest.md` / `manifest.json` go to `../visual-check-screenshots/`, a folder next to this tool (one level up, outside both repositories). Override with `--output <dir>` to put them anywhere. Screenshots are grouped into one folder per viewport (`mobile/`, `tablet-portrait/`, `tablet-landscape/`, `desktop/`) and named after the route (`desktop__pricing.png`), so a route and viewport are identifiable from the filename alone. The manifest lists every file downloaded, the HTTP status and any capture failure.

## Optional modes

- **Laravel auto-discovery** — omit both files and pass `--project /path/to/laravel`; the tool reads `php artisan route:list --json` and captures every GET page. Sample path parameters are resolved from a real link when possible; unresolved ones are listed as skipped.
- **Authenticated pages** — pass `--email` and `--password` together, plus `--login-url`, `--email-selector`, `--password-selector`, `--submit-selector` and `--success-url` if the site is not the Laravel starter kit.
- **Filtering / sizing** — `--only` and `--skip` take regexes; `--viewports mobile,tablet-portrait,tablet-landscape,desktop` selects a subset (375, 768, 1024 and 1440 px wide).
- **Custom readiness** — `--ready-js` takes a JS predicate that returns true when the page is ready; by default it waits for `document.readyState === 'complete'` (Laravel auto-discovery uses the Inertia mount predicate instead). Capture also waits for `load` and `document.fonts.ready`, then `--settle-ms` ms (default 800) before the screenshot.

## Why a separate repo

This is a developer tool, not application code. Its dependencies stay out of any target project and its artifact directory is gitignored, so it can be cloned next to any codebase.
