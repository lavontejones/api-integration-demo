"""SQLite repository. Intake and its first audit entries use one transaction."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DuplicateError(Exception):
    def __init__(self, reason: str, existing_id: str):
        self.reason = reason
        self.existing_id = existing_id
        super().__init__(reason)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS customers (
 id TEXT PRIMARY KEY, company TEXT NOT NULL, company_key TEXT NOT NULL,
 first_name TEXT NOT NULL, last_name TEXT NOT NULL, email TEXT NOT NULL,
 phone TEXT NOT NULL, location TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS ix_customers_company_phone ON customers(company_key, phone);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, external_reference TEXT UNIQUE,
 customer_id TEXT NOT NULL REFERENCES customers(id), request_type TEXT NOT NULL,
 service_interest TEXT NOT NULL, estimated_value_cents INTEGER NOT NULL,
 urgency TEXT NOT NULL, lead_source TEXT NOT NULL, title TEXT NOT NULL,
 notes TEXT NOT NULL, submitted_at TEXT NOT NULL, created_at TEXT NOT NULL,
 assigned_team TEXT NOT NULL, status TEXT NOT NULL, routing_reason TEXT NOT NULL,
 duplicate_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_requests_customer_created ON requests(customer_id, created_at);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
 kind TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(id),
 attempt_number INTEGER NOT NULL, outcome TEXT NOT NULL, http_status INTEGER,
 error_code TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES requests(id),
 action TEXT NOT NULL, before_status TEXT, after_status TEXT, reason TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def add_audit(self, db: sqlite3.Connection, request_id: str, action: str,
                  reason: str, before: str | None = None, after: str | None = None) -> None:
        db.execute("INSERT INTO audit_logs(request_id, action, before_status, after_status, reason, created_at) VALUES(?,?,?,?,?,?)",
                   (request_id, action, before, after, reason, utc_now()))

    def create(self, data: dict) -> dict:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            exact = db.execute("SELECT id FROM requests WHERE request_id=?", (data["request_id"],)).fetchone()
            if exact:
                raise DuplicateError("request_id already exists", exact["id"])
            if data["external_reference"]:
                exact = db.execute("SELECT id FROM requests WHERE external_reference=?", (data["external_reference"],)).fetchone()
                if exact:
                    raise DuplicateError("external_reference already exists", exact["id"])
            company_key = data["company"].casefold()
            email_matches = db.execute("SELECT id,company_key,first_name,last_name,phone FROM customers WHERE email=?",
                                       (data["email"],)).fetchall()
            phone_matches = db.execute("SELECT id FROM customers WHERE company_key=? AND phone=?",
                                       (company_key, data["phone"])).fetchall()
            email_ids = {row["id"] for row in email_matches}
            phone_ids = {row["id"] for row in phone_matches}
            existing_customer = (len(email_ids) == 1 and email_matches[0]["company_key"] == company_key
                                 and email_matches[0]["first_name"] == data["first_name"]
                                 and email_matches[0]["last_name"] == data["last_name"]
                                 and email_matches[0]["phone"] == data["phone"]
                                 and (not phone_ids or phone_ids == email_ids))
            uncertain_match = bool((email_ids or phone_ids) and not existing_customer)
            customer_id = next(iter(email_ids)) if existing_customer else "cus_" + uuid4().hex[:16]
            now = utc_now()
            if not existing_customer:
                db.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?)",
                           (customer_id, data["company"], company_key, data["first_name"],
                            data["last_name"], data["email"], data["phone"], data["location"], now))
            # A new request from the same customer and category within 24 hours needs review.
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
            recent = db.execute(
                "SELECT id FROM requests WHERE customer_id=? AND request_type=? AND created_at >= ? LIMIT 1",
                (customer_id, data["request_type"], cutoff),
            ).fetchone()
            review = uncertain_match or recent is not None
            duplicate_reason = ("Contact or company match needs review" if uncertain_match else
                                "Same customer and request type within 24 hours" if recent else None)
            from .rules import route
            team, status, routing_reason = route(data, existing_customer, review)
            record_id = "req_" + uuid4().hex[:16]
            db.execute("""INSERT INTO requests
                (id,request_id,external_reference,customer_id,request_type,service_interest,
                 estimated_value_cents,urgency,lead_source,title,notes,submitted_at,created_at,
                 assigned_team,status,routing_reason,duplicate_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, data["request_id"], data["external_reference"] or None, customer_id,
                 data["request_type"], data["service_interest"], data["estimated_value_cents"],
                 data["urgency"], data["lead_source"], data["title"], data["notes"],
                 data["submitted_at"], now, team, status, routing_reason, duplicate_reason))
            self.add_audit(db, record_id, "request_created", "Validated and stored", after=status)
            self.add_audit(db, record_id, "duplicate_check", duplicate_reason or
                           ("Existing customer matched" if existing_customer else "No match"))
            self.add_audit(db, record_id, "routed", routing_reason, after=status)
            return {"id": record_id, "customer_id": customer_id, "existing_customer": existing_customer,
                    "review": review, "duplicate_reason": duplicate_reason, "status": status,
                    "assigned_team": team, "routing_reason": routing_reason}

    def get(self, record_id: str) -> dict | None:
        with closing(self.connect()) as db, db:
            row = db.execute("""SELECT r.*, c.company, c.first_name, c.last_name, c.email,
                c.phone, c.location FROM requests r JOIN customers c ON c.id=r.customer_id
                WHERE r.id=?""", (record_id,)).fetchone()
            return dict(row) if row else None

    def create_event(self, record_id: str, kind: str, payload: dict) -> str:
        event_id = "evt_" + uuid4().hex[:16]
        if "event_id" in payload:
            payload["event_id"] = event_id
        now = utc_now()
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                       (event_id, record_id, kind, "pending", json.dumps(payload, sort_keys=True), now, now))
            self.add_audit(db, record_id, "event_created", kind)
        return event_id

    def mark_notification_generated(self, record_id: str, event_id: str, kind: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("UPDATE events SET state=?,updated_at=? WHERE id=?",
                       ("generated", utc_now(), event_id))
            self.add_audit(db, record_id, "notification_generated", kind)

    def add_attempt(self, record_id: str, event_id: str, number: int, outcome: str,
                    http_status: int | None, error_code: str | None) -> None:
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO webhook_attempts(event_id,attempt_number,outcome,http_status,error_code,created_at) VALUES(?,?,?,?,?,?)",
                       (event_id, number, outcome, http_status, error_code, utc_now()))
            self.add_audit(db, record_id, "webhook_attempt", f"Attempt {number}: {outcome}" +
                           (f" ({error_code})" if error_code else ""))

    def finish_event(self, record_id: str, event_id: str, state: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("UPDATE events SET state=?,updated_at=? WHERE id=?", (state, utc_now(), event_id))
            self.add_audit(db, record_id, "event_finished", state)

    def audit(self, record_id: str) -> list[dict]:
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute(
                "SELECT action,before_status,after_status,reason,created_at FROM audit_logs WHERE request_id=? ORDER BY id",
                (record_id,))]

    def attempts(self, record_id: str) -> list[dict]:
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute("""SELECT a.attempt_number,a.outcome,a.http_status,
                a.error_code,a.created_at FROM webhook_attempts a JOIN events e ON e.id=a.event_id
                WHERE e.request_id=? ORDER BY a.id""", (record_id,))]

    def events(self, record_id: str) -> list[dict]:
        with closing(self.connect()) as db, db:
            return [dict(row) for row in db.execute(
                "SELECT id,kind,state,created_at,updated_at FROM events WHERE request_id=? ORDER BY created_at,id",
                (record_id,))]
