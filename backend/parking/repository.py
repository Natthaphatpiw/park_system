"""Supabase HTTP repository and a persistent local SQLite demo adapter.

The camera never receives the Supabase secret. A scan is committed atomically:
Python calculates fees; PostgreSQL checks the expected active session under a lock.
"""
import json
import sqlite3
from datetime import datetime
from uuid import uuid4

import httpx

from .pricing import calculate_fee


class StoreError(Exception):
    def __init__(self, message: str, status: int = 409):
        self.message, self.status = message, status
        super().__init__(message)


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SupabaseStore:
    def __init__(self, settings):
        key = settings.supabase_secret_key
        self.url = settings.supabase_url + "/rest/v1"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        # Modern sb_secret keys use apikey; only legacy JWT keys belong in Bearer.
        if not key.startswith("sb_secret_"):
            self.headers["Authorization"] = "Bearer " + key

    def request(self, method, path, **kwargs):
        try:
            with httpx.Client(timeout=20, headers=self.headers) as client:
                result = client.request(method, self.url + path, **kwargs)
        except httpx.HTTPError as exc:
            raise StoreError("Database connection failed; retry with the same event_id", 503) from exc
        if result.is_error:
            # Do not return SQL internals, URL, or credentials to callers.
            try:
                error = result.json()
            except ValueError:
                error = {}
            message = error.get("message", "")
            if result.status_code == 409 and error.get("code") == "PT409":
                raise StoreError(message)
            raise StoreError("Database request failed; check server configuration and SQL migration", 503)
        return result.json()

    def active(self, plate, province):
        rows = self.request("GET", "/parking_sessions", params={
            "plate": "eq." + plate, "province": "eq." + province,
            "exit_at": "is.null", "select": "*", "limit": "1",
        })
        return rows[0] if rows else None

    def commit(self, event, expected_id, quote, cooldown):
        return self.request("POST", "/rpc/parking_commit_scan", json={
            "p_event": event, "p_expected_session_id": expected_id,
            "p_quote": quote, "p_cooldown_seconds": cooldown,
        })

    def sessions(self, **filters):
        return self.request("POST", "/rpc/parking_list_sessions", json={"p_" + key: value for key, value in filters.items()})

    def dashboard(self, start_date, end_date):
        return self.request("POST", "/rpc/parking_dashboard", json={"p_start_date": start_date, "p_end_date": end_date})


class SQLiteStore:
    """Local demo only. BEGIN IMMEDIATE serializes scan updates across threads."""
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, plate TEXT, province TEXT,
                  occurred_at TEXT, action TEXT, payload TEXT NOT NULL, response TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def all_sessions(self, db=None):
        if db is not None:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM sessions")]
        with self.connect() as conn:
            return self.all_sessions(conn)

    def active(self, plate, province):
        return next((s for s in self.all_sessions() if s["plate"] == plate and s["province"] == province and not s["exit_at"]), None)

    def commit(self, event, expected_id, quote, cooldown):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
            previous = db.execute("SELECT payload,response FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if previous:
                if previous[0] != payload:
                    raise StoreError("event_id already used with different input")
                return dict(json.loads(previous[1]), replayed=True)
            sessions = self.all_sessions(db)
            active = next((s for s in sessions if s["plate"] == event["plate"] and s["province"] == event["province"] and not s["exit_at"]), None)
            if (active["id"] if active else None) != expected_id:
                raise StoreError("state_changed")
            # ISO strings may mix full seconds/fractions; compare actual instants,
            # not lexical order (e.g. '00Z' sorts after '00.1Z'). Demo datasets are small.
            accepted = db.execute("SELECT occurred_at,response FROM events WHERE plate=? AND province=? AND action!='ignored'", (event["plate"], event["province"])).fetchall()
            last = max(accepted, key=lambda row: stamp(row[0]), default=None)
            when = stamp(event["occurred_at"])
            if last and when < stamp(last[0]):
                raise StoreError("Scan is older than the last accepted event")
            if active and active["vehicle_type"] != event["vehicle_type"]:
                raise StoreError("Vehicle type differs from the entry record")
            reason = None
            if active and event["direction"] == "entry":
                reason = "already_parked"
            elif last and event["direction"] == "auto" and (when - stamp(last[0])).total_seconds() < cooldown:
                reason = "cooldown"
            if reason:
                result = {"action": "ignored", "reason": reason, "session": active or json.loads(last[1])["session"], "replayed": False}
            elif active:
                if quote is None:
                    raise StoreError("Missing exit quote")
                verified = calculate_fee(stamp(active["entry_at"]), when, active["vehicle_type"])
                if verified != quote:
                    raise StoreError("Invalid exit quote")
                active.update(quote, exit_at=event["occurred_at"])
                db.execute("UPDATE sessions SET data=? WHERE id=?", (json.dumps(active, ensure_ascii=False), active["id"]))
                result = {"action": "exit", "session": active, "replayed": False}
            else:
                if event["direction"] == "exit":
                    raise StoreError("No active parking session for this plate and province")
                session = {"id": str(uuid4()), "plate": event["plate"], "province": event["province"],
                    "vehicle_type": event["vehicle_type"], "entry_at": event["occurred_at"], "exit_at": None,
                    "duration_seconds": None, "billable_hours": None, "parking_fee": None,
                    "fine_amount": None, "total_amount": None, "overnight_count": None,
                    "policy_version": None, "is_mock": False}
                db.execute("INSERT INTO sessions VALUES(?,?)", (session["id"], json.dumps(session, ensure_ascii=False)))
                result = {"action": "entry", "session": session, "replayed": False}
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)", (event["event_id"], event["plate"], event["province"], event["occurred_at"], result["action"], payload, json.dumps(result, ensure_ascii=False)))
            return result

    def sessions(self, status, limit, offset, plate, start_date, end_date):
        from .analytics import filter_sessions
        rows = filter_sessions(self.all_sessions(), status, plate, start_date, end_date)
        return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def dashboard(self, start_date, end_date):
        from .analytics import summarize
        return summarize(self.all_sessions(), start_date, end_date)
