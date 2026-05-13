# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run commands

### Local dev

```bash
pip3 install -r requirements.txt --break-system-packages   # one-time
cp .env.example .env && $EDITOR .env                       # configure Listmonk credentials
python3 app.py                                             # Flask dev server on http://127.0.0.1:5000
```

### Docker (production / VM deploy)

```bash
cp .env.example .env && $EDITOR .env                       # Listmonk credentials
docker compose up -d                                       # builds image + starts on :5000
docker compose logs -f                                     # tail logs
docker compose down                                        # stop
```

Gunicorn (`-w 1 --threads 4`) is the WSGI server inside the container — single worker so the
`auto_sync` daemon thread is a singleton. DB persists in bind-mounted `./data/mail_exclude.db`.

To upgrade: `git pull && docker compose up -d --build`. To wipe DB: delete `./data/`.

The schema initialises automatically on app start (both modes).

## Purpose

Internal tool for filtering Listmonk vendor lists when an out-of-stock product RFQ comes in. The user picks a list, enters the requester's email, optionally adds ad-hoc exclusions, and gets a filtered BCC list.

## Architecture

**Listmonk is the source of truth** for vendor lists. SQLite is a local read-cache that the user refreshes on demand.

```
Listmonk (server)
    │
    │  GET /api/lists                  → quick list-of-lists (refresh button on /sync)
    │  GET /api/subscribers?list_id=X  → ~20s for 8k subs (per-list sync button)
    ▼
SQLite local cache  (mail_exclude.db)
    │
    │  Read-only fast path (<10ms)
    ▼
Filter wizard at /filter (4 steps, HTMX fragment swaps)
```

**Why a cache and not direct API**: filter wizard's step 3 has a live preview that re-runs the filter on every keystroke (debounced 400ms). Listmonk's `GET /api/subscribers?per_page=all` takes ~20s for an 8k-subscriber list — unusable without caching. Cache is invalidated only when the user clicks **Sync** on the `/sync` tab.

**Asset loading**: `templates/base.html` links self-hosted Tailwind CSS + HTMX when those files exist, else falls back to CDN. `_inject_asset_flags` (in `app.py`) stats `static/css/app.css` and `static/js/htmx.min.js` per request and exposes `has_compiled_css` / `has_local_htmx` to the template. The Dockerfile downloads the Tailwind standalone CLI, builds a minified `app.css`, and fetches `htmx.min.js`, so production never depends on CDN. Local dev can build CSS optionally (see README) or just use CDN.

## Key files

- `app.py` — all Flask routes (filter wizard, sync, permanent excludes); `start_auto_sync()` runs daemon thread
- `wsgi.py` — gunicorn entrypoint; runs `init_schema()` + `start_auto_sync()` on import
- `filters.py` — pure filter logic; `apply_filter()` is the heart of the tool; region classifier via TLD
- `listmonk_client.py` — Listmonk API wrapper (`fetch_lists`, `fetch_subscribers`, `create_list`, `add_subscribers_to_list`); reads `LISTMONK_*` env vars from `.env`
- `sync.py` — orchestrates Listmonk → SQLite refresh (`refresh_lists_index` upserts + prunes, `sync_list` pulls subs)
- `db.py` — SQLite connection + schema; `init_schema()` idempotent with column-add migration. `DB_PATH` honors env var (`/app/data/mail_exclude.db` in Docker, `./mail_exclude.db` locally)
- `templates/` — Jinja2; partials prefixed with `_` are HTMX fragment targets
- `Dockerfile` + `docker-compose.yml` + `.dockerignore` — production deployment

## SQLite schema

```
lists               (id, listmonk_id UNIQUE, name, subscriber_count, last_synced_at)
emails              (id, list_id → lists.id, email)            -- cached subscribers
permanent_excludes  (id, value, type ∈ {email, domain})        -- always-exclude rules
```

`lists.last_synced_at` is `NULL` until the user clicks Sync; the filter wizard only shows lists where it's non-null. `emails` is wiped and re-inserted on each sync (full replace, not diff).

## Filter semantics

Given a source list and a "requester email" `user@example.com`, the result excludes (in priority order, each excluded email counted under exactly one reason):

1. Exact requester email
2. Any email matching requester's domain (`example.com`)
3. Permanent email excludes
4. Permanent domain excludes (suffix match on the part after `@`)
5. Ad-hoc emails entered in step 3
6. Ad-hoc domains entered in step 3

Domain matching is suffix-based, case-insensitive. Permanent excludes apply to every filter run; ad-hoc apply only to the current run.

## UI structure

Three tabs:

- **Lọc & Gửi** (`/filter`) — 4-step wizard wrapped in a single `<form id="wizard-form">`. HTMX swaps `#wizard-body` content per step; hidden inputs (`list_id`, `requester`, `extra_emails`, `extra_domains`) carry state forward. Sidebar progress indicator updates via `htmx:afterSwap` listener reading the `data-step` attribute on each fragment's root `<div>`.
- **Đồng bộ** (`/sync`) — list of Listmonk lists with per-list **Sync** button + last-synced timestamp + cached-vs-server count. Subscriber CRUD (add/remove individual subscribers) is **not** in this app — do that in Listmonk's UI.
- **Exclude vĩnh viễn** (`/excludes`) — two columns (emails / domains), batch add/remove.

Step 3 of the filter wizard shows a live preview of remaining-vs-excluded counts as the user types.

## Conventions

- **UI text is English** (end users are English-speaking). Code identifiers, comments, and any internal logs stay in English. The developer working on this tool collaborates in Vietnamese with Claude, but UI strings must be English.
- Normalize emails to lowercase + strip whitespace at every ingress (sync, batch paste, exclusion input). Store lowercase only.
- Reject syntactically invalid emails at the boundary; don't store and silently drop later.
- Listmonk credentials live only in `.env` (gitignored). Never log them or hardcode them.
