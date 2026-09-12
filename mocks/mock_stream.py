"""Local test double for the Hasen-Stream backend endpoints.

Serves the same contract as src/backend/main.py:
  GET  /mjpeg           -> MJPEG stream (multipart/x-mixed-replace; boundary=frame)
  POST /image/save      -> captures a frame, saves it to ./saved_images, returns JSON
  GET  /saved_images/.. -> saved frames

Usage:
  python -m mocks.mock_stream --images /path/to/image/directory --port 8000
"""
import argparse
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
SAVE_DIRECTORY = os.path.abspath("saved_images")


class MockStream:
    def __init__(self, images):
        self.images = [os.path.abspath(p) for p in images]
        if not self.images:
            raise SystemExit("No images found in the given directory")
        self.lock = threading.Lock()
        self.running = True

    def stop(self):
        self.running = False

    def random_frame(self):
        with open(random.choice(self.images), "rb") as f:
            return f.read()


def build_handler(mock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, payload, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/mjpeg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                while mock.running:
                    try:
                        self.wfile.write(BOUNDARY)
                        self.wfile.write(mock.random_frame())
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(0.2)
            elif self.path.startswith("/saved_images/"):
                name = os.path.basename(self.path)
                file_path = os.path.join(SAVE_DIRECTORY, name)
                if not os.path.exists(file_path):
                    self.send_error(404)
                    return
                with open(file_path, "rb") as f:
                    self._send(f.read(), "image/jpeg")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/image/save":
                os.makedirs(SAVE_DIRECTORY, exist_ok=True)
                file_path = os.path.join(SAVE_DIRECTORY, f"image_{int(time.time())}.jpg")
                with open(file_path, "wb") as f:
                    f.write(mock.random_frame())
                payload = json.dumps(
                    {"message": "Image saved successfully", "file_path": file_path}
                ).encode()
                self._send(payload, "application/json", status=201)
            else:
                self.send_error(404)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Mock the Hasen-Stream backend for local testing.")
    parser.add_argument("--images", required=True, help="Directory containing JPEG frames to serve")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    images = [
        os.path.join(args.images, p)
        for p in os.listdir(args.images)
        if p.lower().endswith((".jpg", ".jpeg"))
    ]
    mock = MockStream(images)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(mock))
    print(f"Mock Hasen-Stream serving {len(images)} frames on http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mock.stop()
        server.server_close()


if __name__ == "__main__":
    main()
