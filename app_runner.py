from __future__ import annotations

import argparse
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from typing import Any, Sequence


def run_app(backend: Any, argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local JNovels EPUB search cache")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(argv)

    backend.initialize_db()
    server = ThreadingHTTPServer((args.host, args.port), backend.AppHandler)
    backend.start_interrupted_sync_recovery()
    url = f"http://{args.host}:{args.port}"
    print(f"JNovels Cache is running at {url}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
