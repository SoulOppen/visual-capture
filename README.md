# route-shots

Reusable screenshot downloader. Give it a list of URLs (or paths plus a base URL) and it renders each one in a real Chromium browser at four viewports, saving full-page PNGs plus a `report.md` / `report.json`. It has no dependency on Laravel or on any particular framework; route auto-discovery for Laravel is an optional convenience.

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
uv run visual-audit --routes-file ./pages.json --base-url https://example.com
```

Or use a plain text file with one absolute URL per line:

```bash
uv run visual-audit --urls-file ./urls.txt
```

By default the PNGs and reports go to `../visual-check-screenshots/`, a folder next to this tool (one level up, gitignored by nothing because it lives outside both repositories). Override with `--output <dir>` to put them anywhere. Screenshots are grouped into one folder per viewport (`mobile/`, `tablet-portrait/`, `tablet-landscape/`, `desktop/`) and named after the route (`desktop__pricing.png`), so a route and viewport are identifiable from the filename alone.

### Optional modes

- **Laravel auto-discovery** — omit both files and pass `--project /path/to/laravel`; the tool reads `php artisan route:list --json` and captures every GET page. Sample path parameters are resolved from a real link when possible; unresolved ones are listed as skipped.
- **Authenticated pages** — pass `--email` and `--password` together, plus `--login-url`, `--email-selector`, `--password-selector`, `--submit-selector` and `--success-url` if the site is not the Laravel starter kit.
- **Audit instead of plain download** — drop `--download-only` to get overflow, console/page error counts and a heuristic brand-color ratio relative to `/` **within the same viewport** (per-viewport baselines and thresholds are in `report.json`). Add `--strict` for a nonzero exit when defects are found.
- **Filtering / sizing** — `--only` and `--skip` take regexes; `--viewports mobile,tablet-portrait,tablet-landscape,desktop` selects a subset (375, 768, 1024 and 1440 px wide).
- **Custom readiness** — `--ready-js` takes a JS predicate that returns true when the page is ready; by default it waits for `document.readyState === 'complete'` (Laravel auto-discovery uses the Inertia mount predicate instead). Capture also waits for `load` and `document.fonts.ready`, then `--settle-ms` ms (default 800) before the screenshot.

## Why a separate repo

This is a developer tool, not application code. Its dependencies stay out of any target project and its artifact directory is gitignored, so it can be copied or cloned next to any codebase.
