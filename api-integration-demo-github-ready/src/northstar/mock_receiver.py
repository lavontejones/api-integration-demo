"""Local-only receiver with deterministic success and failure routes."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Receiver(BaseHTTPRequestHandler):
    counts: dict[str, int] = {}

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 32768:
                raise ValueError
            event = json.loads(self.rfile.read(length))
            event_id = event["event_id"]
            if not isinstance(event_id, str):
                raise ValueError
        except (ValueError, KeyError, UnicodeDecodeError):
            return self.reply(400, {"accepted": False})
        self.counts[event_id] = self.counts.get(event_id, 0) + 1
        if self.path == "/webhook/timeout":
            time.sleep(2)
            return self.reply(200, {"accepted": True, "event_id": event_id})
        if self.path == "/webhook/fail":
            return self.reply(503, {"accepted": False})
        if self.path == "/webhook/reject":
            return self.reply(400, {"accepted": False})
        if self.path == "/webhook/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/webhook")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/webhook/flaky" and self.counts[event_id] < 3:
            return self.reply(503, {"accepted": False})
        if self.path == "/webhook/malformed":
            return self.reply(200, b"not-json")
        if self.path not in {"/webhook/success", "/webhook/flaky"}:
            return self.reply(404, {"accepted": False})
        return self.reply(200, {"accepted": True, "event_id": event_id})

    def reply(self, status: int, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except BrokenPipeError:
            pass  # Expected after a timeout test.


def main():
    parser = argparse.ArgumentParser(description="Local webhook test receiver")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Receiver)
    print(f"Local mock receiver listening on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
