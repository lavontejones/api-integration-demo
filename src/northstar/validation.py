"""Validate first, then normalize. No raw payload is written to logs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

REQUEST_TYPES = {"sales", "service", "billing", "general"}
URGENCIES = {"low", "normal", "high", "urgent"}
SOURCES = {"website", "phone", "referral", "email", "partner"}
EMAIL = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


class ValidationError(Exception):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("Invalid request")


def clean(value: Any) -> str:
    return " ".join(value.strip().split()) if isinstance(value, str) else ""


def normalize(payload: Any, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError([{"field": "body", "message": "Expected a JSON object"}])
    errors: list[dict[str, str]] = []
    allowed = {
        "request_id", "first_name", "last_name", "company", "title", "email", "phone",
        "location", "request_type", "service_interest", "estimated_value", "urgency",
        "lead_source", "notes", "external_reference", "submitted_at",
    }
    for key in payload.keys() - allowed:
        errors.append({"field": key, "message": "Unknown field"})
    data: dict[str, Any] = {}
    for field in ("request_id", "first_name", "last_name", "company", "email", "phone", "location", "request_type", "service_interest", "urgency", "lead_source"):
        data[field] = clean(payload.get(field))
        if not data[field]:
            errors.append({"field": field, "message": "Required text field"})
    for field in ("title", "notes", "external_reference"):
        data[field] = clean(payload.get(field))
    for field in ("first_name", "last_name", "company", "location", "service_interest", "title"):
        if len(data.get(field, "")) > 120:
            errors.append({"field": field, "message": "Must contain at most 120 characters"})
    if len(data["notes"]) > 1000:
        errors.append({"field": "notes", "message": "Must contain at most 1000 characters"})
    if data["request_id"] and not ID.fullmatch(data["request_id"]):
        errors.append({"field": "request_id", "message": "Use 3 to 64 letters, numbers, dots, underscores, or hyphens"})
    if data["external_reference"] and not ID.fullmatch(data["external_reference"]):
        errors.append({"field": "external_reference", "message": "Use 3 to 64 letters, numbers, dots, underscores, or hyphens"})
    data["first_name"] = data["first_name"].title()
    data["last_name"] = data["last_name"].title()
    data["company"] = data["company"].title()
    data["location"] = data["location"].title()
    data["email"] = data["email"].lower()
    if data["email"] and (len(data["email"]) > 254 or not EMAIL.fullmatch(data["email"])):
        errors.append({"field": "email", "message": "Enter a valid email address"})
    digits = re.sub(r"\D", "", data["phone"])
    if data["phone"] and not re.fullmatch(r"[+().\-\s\d]+", data["phone"]):
        errors.append({"field": "phone", "message": "Use digits and standard phone punctuation"})
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if data["phone"] and len(digits) != 10:
        errors.append({"field": "phone", "message": "Use a 10-digit US or Canada phone number"})
    data["phone"] = "+1" + digits if len(digits) == 10 else data["phone"]
    for field, accepted in (("request_type", REQUEST_TYPES), ("urgency", URGENCIES), ("lead_source", SOURCES)):
        data[field] = data[field].lower()
        if data[field] and data[field] not in accepted:
            errors.append({"field": field, "message": "Allowed: " + ", ".join(sorted(accepted))})
    raw_value = payload.get("estimated_value")
    try:
        if isinstance(raw_value, bool) or raw_value is None:
            raise InvalidOperation
        value = Decimal(str(raw_value))
        if not value.is_finite() or value < 0 or value > 1000000 or value.as_tuple().exponent < -2:
            raise InvalidOperation
        data["estimated_value_cents"] = int(value * 100)
    except (InvalidOperation, ValueError):
        errors.append({"field": "estimated_value", "message": "Use a number from 0 to 1000000 with at most two decimal places"})
    raw_date = payload.get("submitted_at")
    try:
        if raw_date is None:
            submitted = now or datetime.now(timezone.utc)
        elif not isinstance(raw_date, str):
            raise ValueError
        else:
            submitted = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if submitted.tzinfo is None:
                raise ValueError
        data["submitted_at"] = submitted.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        errors.append({"field": "submitted_at", "message": "Use an ISO 8601 timestamp with a time zone"})
    if errors:
        raise ValidationError(sorted(errors, key=lambda item: item["field"]))
    return data
