# Mail Exclude Tool

Internal tool for filtering [Listmonk](https://listmonk.app/) vendor lists when
sending RFQ (Request-For-Quote) campaigns. When a vendor emails asking to buy
something you're out of stock on, the tool lets you build a fresh recipient
list — drawn from your existing Listmonk lists — with the requester (and any
unwanted domains/regions) cleanly excluded, then pushes that list back into
Listmonk where you compose & send the campaign.

## Features

- **5-step wizard** — pick source lists → optional region filter (China / Middle East)
  → exclude requester emails → exclude domains → review + push
- **Multi-list source** — union of multiple Listmonk lists, deduped
- **Region filter** — auto-classify subscribers by TLD (`.cn .hk` → China;
  `.ae .sa .il .tr ...` → Middle East). Useful for "China-only" or "non-China"
  RFQ targeting
- **Smart exclusions** — exclude individual emails, whole domains, or maintain
  a persistent always-exclude list
- **Public domain warnings** — refuses to silently exclude `gmail.com` /
  `yahoo.com` etc. (would wipe unrelated subscribers)
- **Autocomplete + orphan detection** — type-ahead from source list; flags
  entries that won't match anything
- **Push to Listmonk** — one click creates a new list with the filtered
  subscribers, ready for you to attach a campaign to
- **Auto-sync** — starred Listmonk lists re-sync every 5 minutes in the
  background

## Quick start — Docker (recommended for VM deployment)

```bash
git clone git@github.com:todo1991/Mail-Exclude-Tool.git
cd Mail-Exclude-Tool
cp .env.example .env
$EDITOR .env                # fill in LISTMONK_URL / USER / TOKEN
docker compose up -d
docker compose logs -f      # tail logs; check for "[auto-sync] started"
```

Open `http://<host>:5000` in a browser. To upgrade later:

```bash
git pull
docker compose up -d --build
```

DB persists in `./data/mail_exclude.db` on the host. Back up by copying the
`data/` directory.

## Quick start — Local Python (for development)

```bash
pip3 install -r requirements.txt          # add --break-system-packages on macOS if needed
cp .env.example .env
$EDITOR .env                              # fill in LISTMONK_URL / USER / TOKEN
python3 app.py                            # Flask dev server on http://127.0.0.1:5000
```

Auto-reloads on file changes (debug mode). DB lives at `./mail_exclude.db`.

## Configuration

`.env` (gitignored — never commit):

```
LISTMONK_URL=https://listmonk.example.com
LISTMONK_USER=api_user
LISTMONK_TOKEN=...
```

Optional override:

```
DB_PATH=/custom/path/mail_exclude.db    # default: ./mail_exclude.db (Docker sets /app/data/...)
```

The Listmonk credentials are a [Listmonk API user](https://listmonk.app/docs/apis/api-users/)
with permission to read lists/subscribers and create lists.

## Workflow

After starting the app:

1. **Sync tab** — click **🔄 Refresh list index** to pull list metadata from
   Listmonk. Click **★** on the lists you want available for filtering. Click
   **⬇ Sync** to pull subscriber data into the local cache (one-time per list;
   starred lists auto-resync every 5 min).

2. **Filter & Send tab** — 5-step wizard:
   - **Step 1**: tick one or more synced source lists
   - **Step 2**: pick a region filter (default: All regions)
   - **Step 3**: enter the email(s) of the requester(s) to exclude
   - **Step 4**: optionally add domain-level excludes (chips suggest
     requesters' domains; public-domain warning protects against
     accidental `@gmail.com` excludes)
   - **Step 5**: review the result, then **Create list in Listmonk** with a
     name of your choice. Open the new list in Listmonk → **New Campaign** →
     pick the list → set up subject/body → send.

3. **Permanent Excludes tab** — emails/domains here are always excluded on
   every filter run. Useful for blacklists / competitors / unsubscribes that
   Listmonk doesn't filter for you.

## Architecture

```
Listmonk (source of truth)
    │
    │  GET /api/lists                  → metadata refresh
    │  GET /api/subscribers?list_id=X  → on-demand sync (per list, ~20s for 8k subs)
    │  POST /api/lists                 → create new list on push
    │  PUT /api/subscribers/lists      → bulk-attach subscribers
    ▼
SQLite local cache (mail_exclude.db)
    │
    │  Read-only fast path (<10ms) — Listmonk would be too slow for live preview
    ▼
Flask + HTMX wizard at /filter
```

**Why a local cache**: Listmonk's subscriber API takes ~20 seconds for an 8k-subscriber
list. The filter wizard's live preview (Step 4) re-runs filtering on every keystroke,
which would be unusable without caching. The cache is refreshed only when the user
explicitly clicks **Sync** or via the 5-min auto-sync.

**Stack**:
- Backend: Flask + Jinja2, SQLite, `requests` for Listmonk
- Frontend: [HTMX](https://htmx.org/) for partial swaps + [Tailwind CSS](https://tailwindcss.com/) via CDN (no build step)
- WSGI: Gunicorn (`-w 1 --threads 4`) in Docker — single worker so the auto-sync
  thread is a singleton

## Project structure

```
app.py                 # Flask routes (wizard, sync, push, excludes)
wsgi.py                # Gunicorn entrypoint — runs init_schema + start_auto_sync on import
filters.py             # Pure filter logic + region classifier + public-domain list
listmonk_client.py     # Minimal Listmonk REST wrapper
sync.py                # Listmonk → SQLite sync orchestration
db.py                  # Schema + idempotent migrations
static/js/             # autocomplete.js (shared by Email & Domain Exclude steps)
templates/             # Jinja2; partials prefixed with `_` are HTMX fragment targets
  filter/              #   wizard steps + push form/success
  sync/                #   Sync tab (list index + ★ pick + sync buttons)
  excludes/            #   Permanent Excludes tab
Dockerfile             # python:3.13-slim + gunicorn
docker-compose.yml     # bind-mount data/ + env_file: .env
CLAUDE.md              # Notes for Claude Code (architecture, schema, conventions)
```

## Operations

**Backup the DB** — copy the `data/` directory (Docker) or `mail_exclude.db`
file (local).

**Reset the cache** — delete the DB file and restart; the schema rebuilds on
boot, then re-sync lists from the Sync tab.

**Tail logs (Docker)** — `docker compose logs -f`. Auto-sync prints lines
like `[auto-sync] list 4: 1068 subscribers` every 5 minutes for starred lists.

**Listmonk list cleanup** — `Refresh list index` in the Sync tab also prunes
local lists that have been deleted from Listmonk, so renamed/removed lists
don't accumulate as stale rows.

## Notes & limitations

- **TLD-based region classification** only catches `.cn .hk` for China and
  `.ae .sa .qa .kw .om .bh .jo .lb .sy .iq .ye .ir .ps .eg .tr .il` for Middle
  East. Companies in those regions using `.com` won't be auto-classified —
  use the Domain Exclude step to handle specific `.com` domains manually.
- **One worker** in Docker is intentional. The auto-sync thread must be a
  singleton; scaling out would create duplicate sync work.
- **No reverse proxy / TLS** is bundled. For public exposure, put nginx (or
  Caddy / Traefik) in front and terminate TLS there.

## Development

See [CLAUDE.md](./CLAUDE.md) for architecture notes, SQLite schema,
filter semantics, and conventions.
