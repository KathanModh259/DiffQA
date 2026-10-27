"""Simple local HTTP server for the DiffQA testbed web app."""

import http.server
import sys
import threading
from functools import partial
from pathlib import Path
from typing import Tuple

PORT = 3000
DIRECTORY = Path(__file__).parent / "testbed_app"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # SPA routing: unknown paths fall back to index.html
        full_path = Path(self.directory) / self.path.split("?")[0].lstrip("/")
        if not full_path.is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format, *args):
        sys.stdout.write(f"[Testbed App] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


def serve_in_background(directory: Path = DIRECTORY, port: int = 0) -> Tuple[http.server.ThreadingHTTPServer, str]:
    """Serve ``directory`` on a daemon thread; port 0 picks a free port. Caller must ``shutdown()``."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def run_server(port: int = PORT, directory: Path = DIRECTORY):
    with http.server.ThreadingHTTPServer(("", port), partial(Handler, directory=str(directory))) as httpd:
        print(f"DiffQA Testbed Web App running at: http://localhost:{port}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down testbed server.")


if __name__ == "__main__":
    run_server(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
