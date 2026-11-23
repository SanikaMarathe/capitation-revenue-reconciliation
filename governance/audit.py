"""Append-only, hash-chained audit log.

hash_n = sha256(prev_hash + canonical event). Editing or deleting any past event breaks
every later hash, which verify_chain() detects. Payloads carry IDs and amounts, never names/DOB.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from privacy.scanner import FORBIDDEN_KEYS
from schemas.enums import ActorType

GENESIS = "0" * 64


def _canonical(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, default=str, separators=(",", ":"))


def _walk_keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _walk_keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _walk_keys(v)}
    return set()


def append_event(conn, *, actor_type: ActorType, actor_id: str, action: str, entity: str, entity_id: str,
                 payload: dict[str, Any] | None = None, ts: datetime | None = None) -> str:
    payload = payload or {}
    leaked = _walk_keys(payload) & set(FORBIDDEN_KEYS)
    if leaked:
        raise ValueError(f"audit payload may not contain identity fields: {sorted(leaked)}")
    # max(seq), not "ORDER BY seq DESC LIMIT 1": on a persisted DuckDB 2.0.1 file, after deletes and
    # reinserts, the top-1 shortcut returned the second-highest row (found during the UI walkthrough).
    row = conn.execute("SELECT seq, hash FROM audit_log WHERE seq = (SELECT max(seq) FROM audit_log)").fetchone()
    seq, prev_hash = (row[0] + 1, row[1]) if row else (1, GENESIS)
    event = {
        "event_id": uuid.uuid4().hex,
        "ts": (ts or datetime.now()).isoformat(),
        "actor_type": actor_type.value,
        "actor_id": actor_id,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "payload": json.loads(json.dumps(payload, default=str)),
    }
    digest = hashlib.sha256((prev_hash + _canonical(event)).encode("utf-8")).hexdigest()
    conn.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 [seq, event["event_id"], event["ts"], event["actor_type"], actor_id, action, entity, entity_id,
                  json.dumps(event["payload"]), prev_hash, digest])
    return event["event_id"]


def verify_chain(conn) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_seq)."""
    prev = GENESIS
    for seq, event_id, ts, actor_type, actor_id, action, entity, entity_id, payload, prev_hash, digest in conn.execute(
            "SELECT seq, event_id, ts, actor_type, actor_id, action, entity, entity_id, payload, prev_hash, hash "
            "FROM audit_log ORDER BY seq").fetchall():
        event = {"event_id": event_id, "ts": ts.isoformat(), "actor_type": actor_type, "actor_id": actor_id,
                 "action": action, "entity": entity, "entity_id": entity_id, "payload": json.loads(payload)}
        expected = hashlib.sha256((prev + _canonical(event)).encode("utf-8")).hexdigest()
        if prev_hash != prev or digest != expected:
            return False, seq
        prev = digest
    return True, None
