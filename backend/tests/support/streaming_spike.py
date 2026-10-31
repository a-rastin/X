"""B02 experiment: compare buffered/SSE traffic under a 100ms idle deadline.

Run: python -m tests.support.streaming_spike (no credentials or patient input).
This models an idle timeout, not a provider's total request deadline.
"""

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for _ in range(10):
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        time.sleep(0.04)
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    time.sleep(0.4)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        results = {}
        for streaming in (False, True):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=0.1)
            started = time.monotonic()
            try:
                conn.request("POST", "/v1/chat/completions", json.dumps({"stream": streaming}))
                response = conn.getresponse()
                chunks = []
                while chunk := response.read1(1024):
                    chunks.append(chunk)
                assert b"[DONE]" in b"".join(chunks)
                result = "completed"
            except TimeoutError:
                result = "idle_timeout"
            finally:
                conn.close()
            results[str(streaming)] = {
                "result": result,
                "elapsed": round(time.monotonic() - started, 3),
            }
        assert results["False"]["result"] == "idle_timeout"
        assert results["True"]["result"] == "completed"
        print(json.dumps(results, sort_keys=True))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
