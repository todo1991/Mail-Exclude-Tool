"""Minimal Listmonk API wrapper."""
import os
from pathlib import Path
from typing import Iterator

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


class ListmonkError(RuntimeError):
    pass


def _config() -> tuple[str, tuple[str, str]]:
    url = os.environ.get("LISTMONK_URL", "").rstrip("/")
    user = os.environ.get("LISTMONK_USER", "")
    token = os.environ.get("LISTMONK_TOKEN", "")
    if not (url and user and token):
        raise ListmonkError("Missing LISTMONK_URL / LISTMONK_USER / LISTMONK_TOKEN in .env")
    return url, (user, token)


def _get(path: str, **params) -> dict:
    base, auth = _config()
    r = requests.get(f"{base}{path}", auth=auth, params=params, timeout=60)
    r.raise_for_status()
    return r.json().get("data", {})


def _send(method: str, path: str, body: dict) -> dict:
    base, auth = _config()
    r = requests.request(method, f"{base}{path}", auth=auth, json=body, timeout=120)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text
        except Exception:
            msg = r.text
        raise ListmonkError(f"{r.status_code}: {msg}")
    return r.json().get("data", {})


def _post(path: str, body: dict) -> dict:
    return _send("POST", path, body)


def _put(path: str, body: dict) -> dict:
    return _send("PUT", path, body)


def fetch_lists() -> list[dict]:
    """All Listmonk lists (id, name, subscriber_count)."""
    data = _get("/api/lists", per_page="all")
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "subscriber_count": item.get("subscriber_count", 0),
        }
        for item in data.get("results", [])
    ]


def fetch_subscribers(list_id: int) -> Iterator[dict]:
    """Yield {'id': int, 'email': str} for each subscriber in a Listmonk list."""
    data = _get("/api/subscribers", list_id=list_id, per_page="all")
    for sub in data.get("results", []):
        email = (sub.get("email") or "").strip().lower()
        sid = sub.get("id")
        if email and sid:
            yield {"id": sid, "email": email}


def create_list(name: str, description: str = "") -> int:
    """POST /api/lists → returns new list id."""
    data = _post("/api/lists", {
        "name": name,
        "type": "private",
        "optin": "single",
        "description": description,
    })
    new_id = data.get("id")
    if not new_id:
        raise ListmonkError(f"create_list: response missing id ({data!r})")
    return new_id


def add_subscribers_to_list(subscriber_ids: list[int], list_id: int) -> int:
    """PUT /api/subscribers/lists → bulk-attach subscribers to a list. Returns count sent."""
    if not subscriber_ids:
        return 0
    _put("/api/subscribers/lists", {
        "ids": subscriber_ids,
        "action": "add",
        "target_list_ids": [list_id],
        "status": "confirmed",
    })
    return len(subscriber_ids)
