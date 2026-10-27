"""Simple local HTTP server for the DiffQA testbed web app."""

import http.server
import socketserver
import os
import sys
from pathlib import Path

PORT = 3000
DIRECTORY = Path(__file__).parent / "testbed_app"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def do_GET(self):
        # SPA routing: if path doesn't point to an existing file with extension, serve index.html
        path_without_query = self.path.split("?")[0]
        full_path = DIRECTORY / path_without_query.lstrip("/")
        if not full_path.exists() or full_path.is_dir():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format, *args):
        # Clean logging
        sys.stdout.write(f"[Testbed App] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()


def run_server(port: int = PORT):
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"⚡ DiffQA Testbed Web App running at: http://localhost:{port}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down testbed server.")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_server(port)
