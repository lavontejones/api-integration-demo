"""Business orchestration, CRM projection, notification, and audit history."""

import logging
import sqlite3
from decimal import Decimal

from .store import DuplicateError, Store
from .validation import ValidationError, normalize
from .webhooks import dispatch


def log_event(**fields):
    import json
    logging.getLogger("northstar").info(json.dumps(fields, sort_keys=True))


def crm_record(row: dict) -> dict:
    return {
        "record_id": row["id"],
        "customer_id": row["customer_id"],
        "contact": {"first_name": row["first_name"], "last_name": row["last_name"],
                    "email": row["email"], "phone": row["phone"], "title": row["title"]},
        "company": row["company"], "location": row["location"],
        "request_type": row["request_type"], "service_interest": row["service_interest"],
        "lead_source": row["lead_source"],
        "estimated_value": f'{Decimal(row["estimated_value_cents"]) / 100:.2f}', "currency": "USD",
        "assigned_team": row["assigned_team"], "priority": row["urgency"],
        "status": row["status"], "submitted_at": row["submitted_at"],
        "created_at": row["created_at"],
        "tags": [row["request_type"], row["urgency"], row["lead_source"]],
        "summary": f'{row["request_type"].title()} request for {row["service_interest"]} from {row["company"]}',
    }


class Service:
    def __init__(self, store: Store, config, sender=None, sleeper=None):
        self.store = store
        self.config = config
        self.sender = sender
        self.sleeper = sleeper

    def intake(self, payload: dict) -> tuple[int, dict]:
        log_event(action="request_received")
        try:
            data = normalize(payload)
        except ValidationError as exc:
            log_event(action="validation_failed", fields=[error["field"] for error in exc.errors])
            return 422, {"error": {"code": "validation_error", "details": exc.errors}}
        log_event(action="validation_passed")
        try:
            result = self.store.create(data)
        except DuplicateError as exc:
            log_event(action="duplicate_rejected", reason=exc.reason)
            return 409, {"error": {"code": "duplicate_request", "message": exc.reason,
                                   "existing_record_id": exc.existing_id}}
        except sqlite3.Error:
            log_event(action="database_error")
            return 503, {"error": {"code": "storage_unavailable", "message": "Record was not stored"}}
        record_id = result["id"]
        log_event(action="duplicate_check", record_id=record_id, review=result["review"])
        log_event(action="database_write", record_id=record_id)
        log_event(action="business_rule_decision", record_id=record_id,
                  team=result["assigned_team"], reason=result["routing_reason"])
        try:
            row = self.store.get(record_id)
            crm = crm_record(row)
            event = {"event_id": "", "event_type": "crm.request.created", "schema_version": 1,
                     "record": crm}
            self.store.create_event(record_id, "crm.request.created", event)
            options = {"sender": self.sender, "sleeper": self.sleeper, "log": log_event}
            options = {key: value for key, value in options.items() if value is not None}
            webhook_state = dispatch(self.store, record_id, event, self.config, **options)
            notification = {
                "channel": "internal_dashboard", "recipient_team": result["assigned_team"],
                "record_id": record_id, "kind": "review_required" if result["review"] else "new_request",
                "message": f'{result["assigned_team"]}: {row["request_type"]} request from {row["company"]}',
            }
            notification_id = self.store.create_event(record_id, "notification.created", notification)
            self.store.mark_notification_generated(record_id, notification_id, notification["kind"])
        except sqlite3.Error:
            log_event(action="database_error", record_id=record_id)
            return 503, {"error": {"code": "processing_incomplete",
                                   "message": "Request was stored but later processing was incomplete",
                                   "record_id": record_id}}
        log_event(action="notification_generated", record_id=record_id)
        return 201, {"record_id": record_id, "customer_id": result["customer_id"],
                     "status": result["status"], "assigned_team": result["assigned_team"],
                     "duplicate_review": result["duplicate_reason"],
                     "webhook_state": webhook_state, "crm_record": crm,
                     "notification": notification,
                     "links": {"audit": f"/v1/requests/{record_id}/audit",
                               "attempts": f"/v1/requests/{record_id}/attempts"}}
