import os
import sys
import threading
import time
from io import BytesIO

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

import filters
import listmonk_client
import sync
from db import connect, init_schema
from listmonk_client import ListmonkError

AUTO_SYNC_INTERVAL_SECONDS = 300  # 5 minutes

app = Flask(__name__)


@app.context_processor
def _inject_asset_flags():
    """Tell templates whether self-hosted assets exist; fall back to CDN if not.

    Per-request stat is fine; static files only check existence (no I/O on hit).
    """
    from pathlib import Path
    static_dir = Path(app.static_folder)
    return {
        "has_compiled_css": (static_dir / "css" / "app.css").is_file(),
        "has_local_htmx": (static_dir / "js" / "htmx.min.js").is_file(),
    }


# ---------------------------------------------------------------- helpers

def list_lists_with_cache() -> list[dict]:
    """Local lists table joined with cached email count."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.listmonk_id, l.name, l.subscriber_count, l.last_synced_at, l.selected,
                   (SELECT COUNT(*) FROM emails e WHERE e.list_id = l.id) AS cached_count_raw,
                   CASE WHEN l.last_synced_at IS NULL THEN NULL
                        ELSE (SELECT COUNT(*) FROM emails e WHERE e.list_id = l.id) END AS cached_count
            FROM lists l
            ORDER BY l.name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def list_lists_for_filter() -> list[dict]:
    """Lists user has marked as selected AND that have local cache — wizard step 1."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.name, COUNT(e.id) AS n
            FROM lists l LEFT JOIN emails e ON e.list_id = l.id
            WHERE l.last_synced_at IS NOT NULL AND l.selected = 1
            GROUP BY l.id ORDER BY l.name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_lists(list_ids: list[int]) -> list[dict]:
    """Multi: return {id, name, listmonk_id} for each valid id (preserve order)."""
    if not list_ids:
        return []
    placeholders = ",".join("?" * len(list_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, name, listmonk_id FROM lists WHERE id IN ({placeholders})",
            list_ids,
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in list_ids if i in by_id]


def list_emails_union(list_ids: list[int]) -> list[str]:
    """Union of emails from multiple lists, deduped, alphabetic."""
    if not list_ids:
        return []
    placeholders = ",".join("?" * len(list_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT email FROM emails WHERE list_id IN ({placeholders}) ORDER BY email",
            list_ids,
        ).fetchall()
    return [r["email"] for r in rows]


def parse_list_ids(form) -> list[int]:
    raw = form.getlist("list_id")
    out: list[int] = []
    seen: set[int] = set()
    for x in raw:
        s = str(x).strip()
        if s.isdigit():
            v = int(s)
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def region_counts(emails: list[str]) -> dict[str, int]:
    """Pre-compute counts for each region mode option."""
    counts = {"cn": 0, "me": 0, "other": 0}
    for e in emails:
        counts[filters.region_of(e)] += 1
    total = sum(counts.values())
    return {
        "all":        total,
        "cn_only":    counts["cn"],
        "me_only":    counts["me"],
        "cn_me_only": counts["cn"] + counts["me"],
        "no_cn":      total - counts["cn"],
        "no_me":      total - counts["me"],
        "no_cn_me":   counts["other"],
    }


def permanent_excludes() -> tuple[list[str], list[str]]:
    with connect() as conn:
        rows = conn.execute("SELECT value, type FROM permanent_excludes").fetchall()
    emails = [r["value"] for r in rows if r["type"] == "email"]
    domains = [r["value"] for r in rows if r["type"] == "domain"]
    return emails, domains


def permanent_excludes_full() -> dict[str, list[dict]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, value, type FROM permanent_excludes ORDER BY type, value"
        ).fetchall()
    return {
        "emails": [dict(r) for r in rows if r["type"] == "email"],
        "domains": [dict(r) for r in rows if r["type"] == "domain"],
    }


def parse_form_lists(form) -> tuple[list[str], list[str]]:
    extra_emails = filters.parse_lines(form.get("extra_emails", ""))
    extra_emails = [e for e in extra_emails if filters.is_valid_email(e)]
    extra_domains = filters.parse_lines(form.get("extra_domains", ""))
    extra_domains = [d for d in extra_domains if "." in d and "@" not in d]
    return extra_emails, extra_domains


def parse_requesters(raw: str) -> tuple[list[str], list[str]]:
    """Split textarea input → (valid_emails, invalid_entries)."""
    candidates = filters.parse_lines(raw)
    valid = [e for e in candidates if filters.is_valid_email(e)]
    invalid = [e for e in candidates if not filters.is_valid_email(e)]
    return valid, invalid


def run_filter(form) -> tuple[filters.FilterResult, list[dict], list[str], dict]:
    list_ids = parse_list_ids(form)
    targets = get_lists(list_ids)
    if not targets:
        abort(400, "invalid list selection")
    requesters, _ = parse_requesters(form.get("requester", ""))
    extra_emails, extra_domains = parse_form_lists(form)
    perm_emails, perm_domains = permanent_excludes()
    region_mode = form.get("region_mode", "all") or "all"
    source_full = list_emails_union(list_ids)
    source_after_region = filters.apply_region_filter(source_full, region_mode)
    result = filters.apply_filter(
        source_after_region, requesters, extra_emails, extra_domains,
        perm_emails, perm_domains,
    )
    meta = {
        "list_ids": list_ids,
        "region_mode": region_mode,
        "source_full_count": len(source_full),
        "source_after_region_count": len(source_after_region),
    }
    return result, targets, requesters, meta


# ---------------------------------------------------------------- routes: filter wizard

@app.route("/")
def index():
    return redirect(url_for("filter_wizard"))


@app.route("/filter", methods=["GET", "POST"])
def filter_wizard():
    preselected_ids = parse_list_ids(request.form) if request.method == "POST" else []
    return render_template(
        "filter/index.html",
        lists=list_lists_for_filter(),
        preselected_ids=preselected_ids,
    )


@app.post("/filter/region")
def filter_region():
    """Step 2: pick region filter. Renders region selector with live counts."""
    list_ids = parse_list_ids(request.form)
    targets = get_lists(list_ids)
    if not targets:
        abort(400, "no lists selected")
    source_emails = list_emails_union(list_ids)
    return render_template(
        "filter/_step_region.html",
        targets=targets,
        list_ids=list_ids,
        source_emails_count=len(source_emails),
        region_counts=region_counts(source_emails),
        region_mode=request.form.get("region_mode") or "all",
    )


@app.post("/filter/email-exclude")
def filter_email_exclude():
    """Step 3: enter emails to exclude."""
    list_ids = parse_list_ids(request.form)
    targets = get_lists(list_ids)
    if not targets:
        abort(400)
    region_mode = request.form.get("region_mode") or "all"
    source_emails = filters.apply_region_filter(list_emails_union(list_ids), region_mode)
    return render_template(
        "filter/_step2.html",
        targets=targets,
        list_ids=list_ids,
        region_mode=region_mode,
        source_emails=source_emails,
        requester=request.form.get("requester", ""),
        extra_domains_value=request.form.get("extra_domains", ""),
    )


@app.post("/filter/domain-exclude")
def filter_domain_exclude():
    """Step 4: enter domains to exclude. Validates the email-exclude input."""
    list_ids = parse_list_ids(request.form)
    targets = get_lists(list_ids)
    if not targets:
        abort(400)
    region_mode = request.form.get("region_mode") or "all"
    raw_requester = request.form.get("requester", "")
    requesters, invalid = parse_requesters(raw_requester)
    source_emails = filters.apply_region_filter(list_emails_union(list_ids), region_mode)
    if not requesters or invalid:
        if invalid:
            err = f"Invalid email: {', '.join(invalid)}"
        else:
            err = "At least 1 valid email is required."
        return render_template(
            "filter/_step2.html",
            targets=targets,
            list_ids=list_ids,
            region_mode=region_mode,
            source_emails=source_emails,
            error=err,
            requester=raw_requester,
            extra_domains_value=request.form.get("extra_domains", ""),
        )
    source_domains = sorted({filters.domain_of(e) for e in source_emails if "@" in e})
    requester_domains = sorted({filters.domain_of(e) for e in requesters})
    domain_chips = [
        {"domain": d, "is_public": filters.is_public_domain(d)} for d in requester_domains
    ]
    return render_template(
        "filter/_step3.html",
        targets=targets,
        list_ids=list_ids,
        region_mode=region_mode,
        requester=raw_requester,
        requesters=requesters,
        domain_chips=domain_chips,
        source_count=len(source_emails),
        source_domains=source_domains,
        extra_domains_value=request.form.get("extra_domains", ""),
    )


@app.post("/filter/preview")
def filter_preview():
    result, _targets, _requesters, meta = run_filter(request.form)
    return render_template(
        "filter/_preview.html",
        result=result,
        source_count=meta["source_after_region_count"],
    )


@app.post("/filter/result")
def filter_result():
    result, targets, requesters, meta = run_filter(request.form)
    perm_emails, perm_domains = permanent_excludes()
    extra_emails, extra_domains = parse_form_lists(request.form)
    return render_template(
        "filter/_step4.html",
        targets=targets,
        list_ids=meta["list_ids"],
        region_mode=meta["region_mode"],
        raw_requester=request.form.get("requester", ""),
        requesters=requesters,
        result=result,
        source_count=meta["source_after_region_count"],
        source_full_count=meta["source_full_count"],
        perm_emails=perm_emails,
        perm_domains=perm_domains,
        extra_domains=extra_domains,
    )


@app.post("/filter/export")
def filter_export():
    result, targets, _requesters, _meta = run_filter(request.form)
    fmt = request.form.get("fmt", "txt")
    if fmt == "csv":
        body = "email\n" + "\n".join(result.kept)
        ext, mime = "csv", "text/csv"
    else:
        body = "\n".join(result.kept)
        ext, mime = "txt", "text/plain"
    base_name = "+".join(t["name"] for t in targets) or "filtered"
    return send_file(
        BytesIO(body.encode("utf-8")),
        mimetype=mime,
        as_attachment=True,
        download_name=f"{base_name}_filtered.{ext}",
    )


# ---------------------------------------------------------------- routes: push to Listmonk

def lookup_subscriber_ids_union(local_list_ids: list[int], emails: list[str]) -> list[int]:
    """Distinct Listmonk subscriber IDs from union of local lists matching the given emails."""
    if not emails or not local_list_ids:
        return []
    email_ph = ",".join("?" * len(emails))
    list_ph = ",".join("?" * len(local_list_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT listmonk_subscriber_id FROM emails "
            f"WHERE list_id IN ({list_ph}) AND email IN ({email_ph}) "
            f"AND listmonk_subscriber_id IS NOT NULL",
            (*local_list_ids, *emails),
        ).fetchall()
    return [r[0] for r in rows]


@app.post("/filter/push-form")
def filter_push_form():
    """Re-render an empty push form (used by 'Create another list' button)."""
    result, _targets, _requesters, _meta = run_filter(request.form)
    return render_template(
        "filter/_push_form.html",
        kept_count=result.kept_count,
        name="",
        description="",
    )


@app.post("/filter/push-to-listmonk")
def filter_push_to_listmonk():
    result, _targets, _requesters, meta = run_filter(request.form)
    name = (request.form.get("list_name", "") or "").strip()
    description = (request.form.get("description", "") or "").strip()

    if not name:
        return render_template(
            "filter/_push_form.html",
            error="List name cannot be empty.",
            kept_count=result.kept_count,
            name=name,
            description=description,
        )
    if result.kept_count == 0:
        return render_template(
            "filter/_push_form.html",
            error="No emails to push.",
            kept_count=0,
            name=name,
            description=description,
        )

    sub_ids = lookup_subscriber_ids_union(meta["list_ids"], result.kept)
    if not sub_ids:
        return render_template(
            "filter/_push_form.html",
            error="No subscriber IDs found — re-sync the source list in the Sync tab.",
            kept_count=result.kept_count,
            name=name,
            description=description,
        )

    try:
        new_list_id = listmonk_client.create_list(name, description)
        added = listmonk_client.add_subscribers_to_list(sub_ids, new_list_id)
    except Exception as e:
        return render_template(
            "filter/_push_form.html",
            error=f"Listmonk error: {e}",
            kept_count=result.kept_count,
            name=name,
            description=description,
        )

    listmonk_base = os.environ.get("LISTMONK_URL", "").rstrip("/")
    return render_template(
        "filter/_push_success.html",
        list_name=name,
        kept_count=result.kept_count,
        added_count=added,
        list_url=f"{listmonk_base}/admin/lists/{new_list_id}" if listmonk_base else "",
    )


# ---------------------------------------------------------------- routes: sync (Listmonk)

def _sync_panel_context(**extras) -> dict:
    return {"lists": list_lists_with_cache(), **extras}


@app.route("/sync")
def sync_page():
    return render_template("sync/index.html", **_sync_panel_context())


@app.post("/sync/refresh")
def sync_refresh_index():
    try:
        r = sync.refresh_lists_index()
        msg = f"Refreshed list index from Listmonk ({r['upserted']} lists)."
        if r["pruned"]:
            msg += f" Pruned {r['pruned']} stale local list(s)."
        return render_template(
            "sync/_panel.html",
            **_sync_panel_context(flash=msg),
        )
    except (ListmonkError, Exception) as e:
        return render_template("sync/_panel.html", **_sync_panel_context(error=str(e)))


@app.post("/sync/list/<int:listmonk_id>")
def sync_list_route(listmonk_id: int):
    try:
        result = sync.sync_list(listmonk_id)
        return render_template(
            "sync/_panel.html",
            **_sync_panel_context(flash=f"Synced: {result['count']:,} subscribers."),
        )
    except (ListmonkError, Exception) as e:
        return render_template("sync/_panel.html", **_sync_panel_context(error=str(e)))


@app.post("/sync/list/<int:listmonk_id>/toggle-selected")
def sync_toggle_selected(listmonk_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT selected FROM lists WHERE listmonk_id = ?", (listmonk_id,)
        ).fetchone()
        if not row:
            abort(404)
        new_state = 0 if row["selected"] else 1
        conn.execute(
            "UPDATE lists SET selected = ? WHERE listmonk_id = ?",
            (new_state, listmonk_id),
        )
    return render_template("sync/_panel.html", **_sync_panel_context())


# ---------------------------------------------------------------- routes: permanent excludes

@app.route("/excludes")
def excludes_page():
    return render_template("excludes/index.html", data=permanent_excludes_full())


@app.post("/excludes/add")
def excludes_add():
    kind = request.form.get("type", "")
    if kind not in ("email", "domain"):
        abort(400)
    raw = request.form.get("values", "")
    candidates = filters.parse_lines(raw)
    if kind == "email":
        valid = [v for v in candidates if filters.is_valid_email(v)]
    else:
        valid = [v for v in candidates if "." in v and "@" not in v]
    added = 0
    if valid:
        with connect() as conn:
            cur = conn.executemany(
                "INSERT OR IGNORE INTO permanent_excludes (value, type) VALUES (?, ?)",
                [(v, kind) for v in valid],
            )
            added = cur.rowcount or 0
    return render_template(
        "excludes/_panel.html",
        data=permanent_excludes_full(),
        flash=f"Added {added}/{len(valid)} {kind}(s).",
    )


@app.delete("/excludes/<int:exclude_id>")
def excludes_delete(exclude_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM permanent_excludes WHERE id = ?", (exclude_id,))
    return render_template("excludes/_panel.html", data=permanent_excludes_full())


# ---------------------------------------------------------------- background: auto-sync starred lists

def _selected_listmonk_ids() -> list[int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT listmonk_id FROM lists WHERE selected = 1"
        ).fetchall()
    return [r["listmonk_id"] for r in rows]


def _auto_sync_loop():
    """Daemon loop: every AUTO_SYNC_INTERVAL_SECONDS, sync each starred list serially."""
    while True:
        time.sleep(AUTO_SYNC_INTERVAL_SECONDS)
        ids = _selected_listmonk_ids()
        if not ids:
            continue
        print(f"[auto-sync] syncing {len(ids)} starred list(s): {ids}", file=sys.stderr)
        for listmonk_id in ids:
            try:
                result = sync.sync_list(listmonk_id)
                print(
                    f"[auto-sync] list {listmonk_id}: {result['count']} subscribers",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[auto-sync] list {listmonk_id} FAILED: {e}", file=sys.stderr)


def start_auto_sync():
    """Start the auto-sync daemon, guarding against werkzeug reloader's parent process."""
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return  # reloader parent — skip, child will start it
    t = threading.Thread(target=_auto_sync_loop, daemon=True, name="auto-sync")
    t.start()
    print(
        f"[auto-sync] started, interval={AUTO_SYNC_INTERVAL_SECONDS}s",
        file=sys.stderr,
    )


# ---------------------------------------------------------------- bootstrap

if __name__ == "__main__":
    init_schema()
    start_auto_sync()
    app.run(debug=True, port=5000)
