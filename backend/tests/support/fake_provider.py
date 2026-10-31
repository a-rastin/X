"""Scriptable local /v1/chat/completions stand-in (no credentials recorded)."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


@contextmanager
def fake_provider(*, cpt, delay=0, status=200, disconnect=False, reasoning_content=False):
    received = threading.Event()
    stop = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            assert self.path == "/v1/chat/completions"
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            received.set()
            if stop.wait(delay):
                return
            message = {"role": "assistant", "content": json.dumps(cpt)}
            if reasoning_content:
                message = {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": json.dumps(cpt),
                }
            body = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body[: len(body) // 2] if disconnect else body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            received=received,
            requests=requests,
        )
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
