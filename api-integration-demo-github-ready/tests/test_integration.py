import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from northstar.api import make_server
from northstar.config import Config
from northstar.mock_receiver import Receiver
from northstar.rules import route
from northstar.store import Store
from northstar.validation import ValidationError, normalize

TOKEN = "synthetic-test-token-only"


def sample(**changes):
    data = {
        "request_id": "synthetic-001", "first_name": "  AVERY  ", "last_name": "  STONE ",
        "company": "  OAK RIDGE DENTAL GROUP ", "title": "Office Manager",
        "email": " AVERY.STONE@EXAMPLE.COM ", "phone": "(202) 555-0101",
        "location": " Austin, TX ", "request_type": " Sales ",
        "service_interest": "  Maintenance contract ", "estimated_value": "35000.00",
        "urgency": " Normal ", "lead_source": " Website ", "notes": "  Synthetic request ",
        "external_reference": "web-001", "submitted_at": "2026-01-15T12:00:00-06:00",
    }
    data.update(changes)
    return data


class TestValidationAndRules(unittest.TestCase):
    def test_normalization(self):
        data = normalize(sample())
        self.assertEqual(data["email"], "avery.stone@example.com")
        self.assertEqual(data["phone"], "+12025550101")
        self.assertEqual(data["company"], "Oak Ridge Dental Group")
        self.assertEqual(data["estimated_value_cents"], 3500000)
        self.assertEqual(data["submitted_at"], "2026-01-15T18:00:00Z")

    def test_invalid_fields(self):
        bad = sample(email="bad", phone="123", urgency="critical", estimated_value="12.345",
                     submitted_at="yesterday")
        with self.assertRaises(ValidationError) as ctx:
            normalize(bad)
        self.assertEqual({item["field"] for item in ctx.exception.errors},
                         {"email", "phone", "urgency", "estimated_value", "submitted_at"})

    def test_routes_are_ordered(self):
        data = normalize(sample())
        self.assertEqual(route(data, False, False)[0], "Senior Sales")
        self.assertEqual(route(data, True, False)[0], "Account Management")
        self.assertEqual(route(data, True, True)[1], "needs_review")
        self.assertEqual(route({**data, "request_type": "billing"}, False, False)[0], "Finance")
        self.assertEqual(route({**data, "request_type": "service", "urgency": "urgent"}, True, False)[0], "Escalations")


class TestHTTPFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.receiver = __import__("http.server").server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        Receiver.counts = {}
        self.receiver_thread = threading.Thread(target=self.receiver.serve_forever, daemon=True)
        self.receiver_thread.start()
        self.config = Config(TOKEN, str(Path(self.temp.name) / "demo.sqlite3"),
                             f"http://127.0.0.1:{self.receiver.server_port}/webhook/success",
                             retry_count=2, timeout_seconds=0.1, retry_delay_seconds=0)
        self.server = make_server("127.0.0.1", 0, self.config)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.receiver.shutdown()
        self.receiver.server_close()
        self.temp.cleanup()

    def call(self, path, body=None, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(body).encode("utf-8")
        else:
            raw = None
        request = Request(self.base + path, data=raw, headers=headers,
                          method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_success_persistence_crm_notification_audit(self):
        status, result = self.call("/v1/requests", sample())
        self.assertEqual(status, 201)
        self.assertEqual(result["assigned_team"], "Senior Sales")
        self.assertEqual(result["webhook_state"], "delivered")
        self.assertEqual(result["crm_record"]["contact"]["phone"], "+12025550101")
        self.assertEqual(result["notification"]["recipient_team"], "Senior Sales")
        record_id = result["record_id"]
        store = Store(self.config.database_path)
        self.assertIsNotNone(store.get(record_id))
        self.assertEqual(store.events(record_id)[0]["state"], "delivered")
        self.assertEqual(store.attempts(record_id)[0]["outcome"], "success")
        self.assertIn("routed", [entry["action"] for entry in store.audit(record_id)])
        self.assertEqual(self.call(f"/v1/requests/{record_id}/crm")[0], 200)
        self.assertEqual(self.call(f"/v1/requests/{record_id}/audit")[0], 200)

    def test_auth_invalid_and_exact_duplicate(self):
        self.assertEqual(self.call("/v1/requests", sample(), token="wrong")[0], 401)
        status, error = self.call("/v1/requests", sample(email="invalid"))
        self.assertEqual(status, 422)
        self.assertEqual(error["error"]["code"], "validation_error")
        self.assertEqual(self.call("/v1/requests", sample())[0], 201)
        status, error = self.call("/v1/requests", sample())
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "duplicate_request")
        status, error = self.call("/v1/requests", sample(request_id="synthetic-002"))
        self.assertEqual(status, 409)  # External reference is also unique.

    def test_review_and_existing_customer(self):
        first = self.call("/v1/requests", sample())[1]
        second = self.call("/v1/requests", sample(request_id="synthetic-002", external_reference="web-002"))[1]
        self.assertEqual(second["status"], "needs_review")
        self.assertEqual(second["notification"]["kind"], "review_required")
        self.assertEqual(second["customer_id"], first["customer_id"])
        third = self.call("/v1/requests", sample(request_id="synthetic-003", external_reference="web-003",
                                                  request_type="general"))[1]
        self.assertEqual(third["assigned_team"], "Account Management")

    def test_phone_only_match_does_not_merge(self):
        first = self.call("/v1/requests", sample())[1]
        uncertain = self.call("/v1/requests", sample(request_id="synthetic-002",
                                                      external_reference="web-002",
                                                      email="other.contact@example.com",
                                                      request_type="billing"))[1]
        self.assertEqual(uncertain["status"], "needs_review")
        self.assertNotEqual(uncertain["customer_id"], first["customer_id"])
        self.assertIn("match needs review", uncertain["duplicate_review"])

    def test_changed_contact_data_needs_review(self):
        first = self.call("/v1/requests", sample())[1]
        changed = self.call("/v1/requests", sample(request_id="synthetic-002",
                                                    external_reference="web-002",
                                                    phone="202-555-0199", request_type="billing"))[1]
        self.assertEqual(changed["status"], "needs_review")
        self.assertNotEqual(changed["customer_id"], first["customer_id"])
        self.assertEqual(changed["crm_record"]["contact"]["phone"], "+12025550199")

    def test_database_failure_has_structured_response(self):
        store = self.server.RequestHandlerClass.service.store
        with patch.object(store, "create", side_effect=sqlite3.OperationalError("synthetic failure")):
            status, result = self.call("/v1/requests", sample())
        self.assertEqual(status, 503)
        self.assertEqual(result["error"]["code"], "storage_unavailable")

    def test_malformed_json_and_wrong_content_type(self):
        for content_type, body, expected in (("application/json", b"{bad", 400),
                                             ("text/plain", b"{}", 415)):
            request = Request(self.base + "/v1/requests", data=body, method="POST",
                              headers={"Authorization": f"Bearer {TOKEN}",
                                       "Content-Type": content_type})
            with self.assertRaises(HTTPError) as ctx:
                urlopen(request, timeout=3)
            self.assertEqual(ctx.exception.code, expected)
            self.assertIn("error", json.load(ctx.exception))

    def test_webhook_flaky_retry_and_final_failure(self):
        object.__setattr__(self.config, "webhook_url",
                           f"http://127.0.0.1:{self.receiver.server_port}/webhook/flaky")
        result = self.call("/v1/requests", sample())[1]
        self.assertEqual(result["webhook_state"], "delivered")
        attempts = Store(self.config.database_path).attempts(result["record_id"])
        self.assertEqual([a["outcome"] for a in attempts], ["failure", "failure", "success"])
        object.__setattr__(self.config, "webhook_url",
                           f"http://127.0.0.1:{self.receiver.server_port}/webhook/fail")
        failed = self.call("/v1/requests", sample(request_id="synthetic-002", external_reference="web-002",
                                                    request_type="billing"))[1]
        self.assertEqual(failed["webhook_state"], "failed")
        self.assertEqual(len(Store(self.config.database_path).attempts(failed["record_id"])), 3)

    def test_timeout_and_malformed_response(self):
        for index, scenario in enumerate(("timeout", "malformed"), 1):
            object.__setattr__(self.config, "webhook_url",
                               f"http://127.0.0.1:{self.receiver.server_port}/webhook/{scenario}")
            result = self.call("/v1/requests", sample(request_id=f"synthetic-00{index}",
                                                      external_reference=f"web-00{index}",
                                                      request_type="billing"))[1]
            self.assertEqual(result["webhook_state"], "failed")
            attempts = Store(self.config.database_path).attempts(result["record_id"])
            self.assertEqual(attempts[0]["error_code"], scenario if scenario == "timeout" else "malformed_response")

    def test_http_400_webhook_is_not_retried(self):
        object.__setattr__(self.config, "webhook_url",
                           f"http://127.0.0.1:{self.receiver.server_port}/webhook/reject")
        result = self.call("/v1/requests", sample())[1]
        self.assertEqual(result["webhook_state"], "failed")
        attempts = Store(self.config.database_path).attempts(result["record_id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["http_status"], 400)

    def test_webhook_redirect_is_not_followed(self):
        object.__setattr__(self.config, "webhook_url",
                           f"http://127.0.0.1:{self.receiver.server_port}/webhook/redirect")
        result = self.call("/v1/requests", sample())[1]
        self.assertEqual(result["webhook_state"], "failed")
        attempts = Store(self.config.database_path).attempts(result["record_id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["http_status"], 302)


if __name__ == "__main__":
    unittest.main()
