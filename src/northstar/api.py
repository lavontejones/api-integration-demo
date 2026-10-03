"""Local REST API. Run with: PYTHONPATH=src python3 -m northstar.api"""

import argparse
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import Config
from .service import Service, crm_record
from .store import Store

MAX_BODY = 32 * 1024


class Handler(BaseHTTPRequestHandler):
    service: Service
    api_token: str

    def log_message(self, format, *args):
        # Do not put request paths or headers in access logs.
        pass

    def respond(self, status: int, body: dict):
        encoded = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self) -> bool:
        candidate = self.headers.get("Authorization", "")
        if not candidate.startswith("Bearer ") or not hmac.compare_digest(candidate[7:], self.api_token):
            self.respond(401, {"error": {"code": "unauthorized", "message": "Valid bearer token required"}})
            return False
        return True

    def do_POST(self):
        if not self.authorized():
            return
        if urlsplit(self.path).path != "/v1/requests":
            return self.respond(404, {"error": {"code": "not_found"}})
        if self.headers.get_content_type() != "application/json":
            return self.respond(415, {"error": {"code": "unsupported_media_type"}})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = 0
        if length < 1 or length > MAX_BODY:
            return self.respond(413, {"error": {"code": "invalid_body_size", "message": "JSON body must be 1 to 32768 bytes"}})
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": {"code": "invalid_json"}})
        status, body = self.service.intake(payload)
        self.respond(status, body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.respond(200, {"status": "ok", "mode": "local_demo"})
        if not self.authorized():
            return
        parts = path.strip("/").split("/")
        if len(parts) not in {3, 4} or parts[:2] != ["v1", "requests"]:
            return self.respond(404, {"error": {"code": "not_found"}})
        record_id = parts[2]
        row = self.service.store.get(record_id)
        if row is None:
            return self.respond(404, {"error": {"code": "not_found"}})
        if len(parts) == 3:
            return self.respond(200, {"crm_record": crm_record(row),
                                      "events": self.service.store.events(record_id)})
        if parts[3] == "crm":
            return self.respond(200, {"crm_record": crm_record(row)})
        if parts[3] == "audit":
            return self.respond(200, {"record_id": record_id, "audit": self.service.store.audit(record_id)})
        if parts[3] == "attempts":
            return self.respond(200, {"record_id": record_id, "attempts": self.service.store.attempts(record_id)})
        self.respond(404, {"error": {"code": "not_found"}})


def make_server(host: str, port: int, config: Config) -> ThreadingHTTPServer:
    service = Service(Store(config.database_path), config)
    handler = type("ConfiguredHandler", (Handler,), {"service": service, "api_token": config.api_token})
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description="Synthetic Northstar intake API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = Config.load()
    logging.basicConfig(level=config.log_level, format="%(message)s")
    server = make_server(args.host, args.port, config)
    print(f"Northstar demo API listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
