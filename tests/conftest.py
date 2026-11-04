"""Тесты никогда не обращаются к настоящей форме: адрес подменяется для каждого теста."""
import http.server
import threading
from pathlib import Path

import pytest
from PIL import Image

from photoapp import form_filler

FAKE_FORM = Path(__file__).with_name("fake_form.html").read_bytes()


@pytest.fixture(autouse=True)
def no_real_form(monkeypatch, tmp_path):
    monkeypatch.setattr(form_filler, "FORM_URL", "about:blank")
    monkeypatch.setattr(form_filler, "HELPER", "111")
    monkeypatch.setattr(form_filler, "MARKS_FILE", tmp_path / "no-marks.json")


@pytest.fixture
def photo(tmp_path) -> Path:
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (200, 150), (120, 130, 140)).save(path)
    return path


@pytest.fixture
def form_server(monkeypatch):
    """Локальная копия формы; submits хранит запросы отправки."""
    submits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FAKE_FORM)

        def do_POST(self):
            submits.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    monkeypatch.setattr(form_filler, "FORM_URL", url)
    yield submits
    server.shutdown()
