import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import keepalive


def test_ping_url(monkeypatch):
    monkeypatch.delenv("PMM_KEEP_AWAKE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert keepalive.ping_url() is None
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://pmm.onrender.com/")
    assert keepalive.ping_url() == "https://pmm.onrender.com/healthz"
    monkeypatch.setenv("PMM_KEEP_AWAKE_URL", "https://mortgages.example.com")
    assert keepalive.ping_url() == "https://mortgages.example.com/healthz"
    monkeypatch.setenv("PMM_KEEP_AWAKE", "0")
    assert keepalive.ping_url() is None


def test_ping_hits_the_health_endpoint():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    assert keepalive.ping(f"http://127.0.0.1:{server.server_port}/healthz")
    assert hits == ["/healthz"]
    assert not keepalive.ping("http://127.0.0.1:9/healthz")  # nothing listening → False, no exception


def test_not_started_in_tests_or_without_url(app, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://pmm.onrender.com")
    assert keepalive.start(app) is False  # TESTING
    app.config["TESTING"] = False
    monkeypatch.setattr(keepalive, "_started", True)
    assert keepalive.start(app) is False  # already running in this process
