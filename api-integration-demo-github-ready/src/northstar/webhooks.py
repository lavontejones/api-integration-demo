"""Outbound webhook with bounded retries and a checked JSON acknowledgement."""

from __future__ import annotations

import json
from http.client import HTTPException
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class WebhookFailure(Exception):
    def __init__(self, code: str, status: int | None = None, retryable: bool = True):
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def send(url: str, event: dict, timeout: float) -> int:
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json",
                                                 "X-Demo-Event-Id": event["event_id"]}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(4096)
    except HTTPError as exc:
        raise WebhookFailure("http_error", exc.code, retryable=exc.code >= 500) from exc
    except (URLError, socket.timeout, TimeoutError, OSError, HTTPException) as exc:
        raise WebhookFailure("timeout" if isinstance(exc, (socket.timeout, TimeoutError)) or
                             isinstance(getattr(exc, "reason", None), socket.timeout) else "connection_error") from exc
    if status < 200 or status >= 300:
        raise WebhookFailure("http_error", status, retryable=status >= 500)
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookFailure("malformed_response", status) from exc
    if not isinstance(parsed, dict) or parsed.get("accepted") is not True or parsed.get("event_id") != event["event_id"]:
        raise WebhookFailure("invalid_acknowledgement", status)
    return status


def dispatch(store, record_id: str, event: dict, config, sender=send, sleeper=time.sleep,
             log=lambda **kwargs: None) -> str:
    for number in range(1, config.retry_count + 2):
        try:
            status = sender(config.webhook_url, event, config.timeout_seconds)
            store.add_attempt(record_id, event["event_id"], number, "success", status, None)
            store.finish_event(record_id, event["event_id"], "delivered")
            log(action="webhook_success", record_id=record_id, attempt=number)
            return "delivered"
        except WebhookFailure as exc:
            store.add_attempt(record_id, event["event_id"], number, "failure", exc.status, exc.code)
            log(action="webhook_failure", record_id=record_id, attempt=number, error_code=exc.code)
            if not exc.retryable or number > config.retry_count:
                store.finish_event(record_id, event["event_id"], "failed")
                log(action="webhook_final_failure", record_id=record_id, error_code=exc.code)
                return "failed"
            log(action="webhook_retry", record_id=record_id, attempt=number + 1)
            sleeper(config.retry_delay_seconds * (2 ** (number - 1)))
    raise AssertionError("Retry loop ended without a result")
