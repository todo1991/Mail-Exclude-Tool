"""Sync Listmonk lists/subscribers into local SQLite cache."""
from datetime import datetime, timezone

import listmonk_client
from db import connect


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def refresh_lists_index() -> dict:
    """Pull list metadata from Listmonk → upsert into local `lists` table,
    and prune any local lists that no longer exist remotely.

    Does NOT pull subscribers; that's the heavy operation. Use sync_list() for that.
    Returns {"upserted": N, "pruned": M}.
    """
    remote = listmonk_client.fetch_lists()
    remote_ids = {item["id"] for item in remote}
    pruned = 0
    with connect() as conn:
        for item in remote:
            conn.execute(
                """
                INSERT INTO lists (listmonk_id, name, subscriber_count)
                VALUES (?, ?, ?)
                ON CONFLICT(listmonk_id) DO UPDATE SET
                    name = excluded.name,
                    subscriber_count = excluded.subscriber_count
                """,
                (item["id"], item["name"], item["subscriber_count"]),
            )
        # Prune local lists no longer in Listmonk.
        # Guarded by `remote_ids` being non-empty so a transient empty response
        # doesn't wipe local state. `emails` rows cascade via FK.
        if remote_ids:
            placeholders = ",".join("?" * len(remote_ids))
            cur = conn.execute(
                f"DELETE FROM lists WHERE listmonk_id NOT IN ({placeholders})",
                tuple(remote_ids),
            )
            pruned = cur.rowcount or 0
    return {"upserted": len(remote), "pruned": pruned}


def sync_list(listmonk_id: int) -> dict:
    """Pull all subscribers (id + email) for a single list → replace local cache."""
    subs = list(listmonk_client.fetch_subscribers(listmonk_id))
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM lists WHERE listmonk_id = ?", (listmonk_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"List {listmonk_id} not in local index — run refresh_lists_index() first")
        local_id = row["id"]
        conn.execute("DELETE FROM emails WHERE list_id = ?", (local_id,))
        if subs:
            conn.executemany(
                """
                INSERT OR IGNORE INTO emails (list_id, email, listmonk_subscriber_id)
                VALUES (?, ?, ?)
                """,
                [(local_id, s["email"], s["id"]) for s in subs],
            )
        conn.execute(
            "UPDATE lists SET subscriber_count = ?, last_synced_at = ? WHERE id = ?",
            (len(subs), utcnow_iso(), local_id),
        )
    return {"list_id": local_id, "count": len(subs)}
