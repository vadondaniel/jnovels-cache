from __future__ import annotations

import json
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import Any

from server_modules.web.api_routes import handle_get_api, handle_post_api


def create_app_handler(backend: Any) -> type[SimpleHTTPRequestHandler]:
    class AppHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(
                *args,
                directory=str(backend.STATIC_DIR),
                **kwargs,
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if handle_get_api(self, backend, parsed):
                return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            handle_post_api(self, backend, parsed)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(
            self,
            payload: Any,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {format % args}")

    return AppHandler
