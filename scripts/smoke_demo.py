"""Run a complete local HTTP demonstration with a temporary database."""

import json
import logging
import tempfile
import threading
from http.server import ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from northstar.api import make_server
from northstar.config import Config
from northstar.mock_receiver import Receiver
from northstar.store import Store


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def post(url, token, payload):
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                      method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def main():
    root = Path(__file__).resolve().parents[1]
    samples = json.loads((root / "samples" / "requests.json").read_text(encoding="utf-8"))
    token = "synthetic-smoke-token-only"
    captured_logs = StringIO()
    logger = logging.getLogger("northstar")
    handler = logging.StreamHandler(captured_logs)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    with tempfile.TemporaryDirectory() as temp:
        database_path = str(Path(temp) / "demo.sqlite3")
        receiver, _ = start(ThreadingHTTPServer(("127.0.0.1", 0), Receiver))
        try:
            for index, scenario in enumerate(("success", "flaky", "fail")):
                config = Config(token, database_path,
                                f"http://127.0.0.1:{receiver.server_port}/webhook/{scenario}",
                                retry_count=2, timeout_seconds=0.3, retry_delay_seconds=0)
                api, _ = start(make_server("127.0.0.1", 0, config))
                try:
                    status, result = post(f"http://127.0.0.1:{api.server_port}/v1/requests", token, samples[index])
                    assert status == 201, result
                    assert result["crm_record"]["company"] == samples[index]["company"]
                    assert result["notification"]["record_id"] == result["record_id"]
                    expected_state = "failed" if scenario == "fail" else "delivered"
                    assert result["webhook_state"] == expected_state
                    store = Store(database_path)
                    assert store.get(result["record_id"]) is not None
                    assert any(item["action"] == "routed" for item in store.audit(result["record_id"]))
                    expected_attempts = 1 if scenario == "success" else 3
                    assert len(store.attempts(result["record_id"])) == expected_attempts
                    print(json.dumps({"scenario": scenario, "http_status": status,
                                      "assigned_team": result.get("assigned_team"),
                                      "webhook_state": result.get("webhook_state")}, sort_keys=True))
                finally:
                    api.shutdown()
                    api.server_close()
            config = Config(token, database_path,
                            f"http://127.0.0.1:{receiver.server_port}/webhook/success")
            api, _ = start(make_server("127.0.0.1", 0, config))
            try:
                url = f"http://127.0.0.1:{api.server_port}/v1/requests"
                status, result = post(url, token, samples[0])
                assert status == 409 and result["error"]["code"] == "duplicate_request"
                print(json.dumps({"scenario": "exact_duplicate", "http_status": status,
                                  "error_code": result.get("error", {}).get("code")}, sort_keys=True))
                invalid = {**samples[3], "request_id": "invalid-001", "email": "invalid-email"}
                status, result = post(url, token, invalid)
                assert status == 422 and result["error"]["code"] == "validation_error"
                print(json.dumps({"scenario": "validation_failure", "http_status": status,
                                  "error_code": result["error"]["code"]}, sort_keys=True))
            finally:
                api.shutdown()
                api.server_close()
        finally:
            receiver.shutdown()
            receiver.server_close()
    assert '"action": "validation_failed"' in captured_logs.getvalue()
    assert '"action": "database_write"' in captured_logs.getvalue()
    assert '"action": "webhook_retry"' in captured_logs.getvalue()
    logger.removeHandler(handler)
    print(json.dumps({"audit": "passed", "logging": "passed", "storage": "passed",
                      "crm_output": "passed", "notification": "passed"}, sort_keys=True))


if __name__ == "__main__":
    main()
