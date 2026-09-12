"""Fetching a single frame from the Hasen-Stream backend.

The stream lives on the camera Pi (https://github.com/FabianSieper/Hasen-Stream)
and is reached over the LAN; RabbitRecognition runs on a second Pi in the same
network. The backend contract consumed here:
  GET  /mjpeg           -> MJPEG stream (multipart/x-mixed-replace; boundary=frame)
  POST /image/save      -> captures a frame, returns {"file_path": ...}
  GET  /saved_images/.. -> saved frames
"""
import os

import cv2
import numpy as np
import requests

# Stop buffering if the stream stays garbage for longer than this without
# a valid frame start marker.
MAX_GARBAGE_BYTES = 1 << 20


def extract_first_jpeg(chunks, boundary="frame"):
    """Consume an iterable of MJPEG response chunks, return first full JPEG.

    ``chunks`` must yield bytes; an empty chunk or None ends the stream.
    Handles frames that start directly after the boundary (Starlette) as
    well as frames preceded by part headers (picamera2 Content-Length).
    """
    start = f"--{boundary}\r\n".encode()
    end = f"\r\n--{boundary}".encode()
    buf = b""
    payload_start = None
    while True:
        chunk = next(chunks)
        if not chunk:
            raise RuntimeError("Stream ended before a full frame arrived")
        buf += chunk
        if payload_start is None:
            idx = buf.find(start)
            if idx == -1:
                if len(buf) > MAX_GARBAGE_BYTES:
                    # Keep enough tail for a split marker, drop the rest.
                    buf = buf[-(len(start) - 1):]
                continue
            data_at = idx + len(start)
            if len(buf) - data_at < 2:
                continue
            if buf[data_at:data_at + 2] == b"\xff\xd8":
                payload_start = data_at
            else:
                # Part headers (e.g. Content-Type/Content-Length) after the
                # boundary. Commit only once the header block is complete,
                # otherwise keep buffering.
                header_end = buf.find(b"\r\n\r\n", data_at)
                if header_end != -1:
                    payload_start = header_end + 4
                elif len(buf) > MAX_GARBAGE_BYTES:
                    raise RuntimeError("Malformed MJPEG stream (unterminated part headers)")
        end_idx = buf.find(end, payload_start)
        if end_idx != -1:
            return buf[payload_start:end_idx]
        if payload_start is not None:
            buf = buf[payload_start:]
            payload_start = 0


def decode_jpeg(jpeg):
    """Decode JPEG bytes to a BGR numpy array."""
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("Failed to decode JPEG frame")
    return frame


def fetch_jpeg_from_mjpeg(base_url, timeout=20.0):
    """Grab the first full MJPEG frame from {base}/mjpeg, return JPEG bytes."""
    url = base_url.rstrip("/") + "/mjpeg"
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        boundary = (
            content_type.split("boundary=")[-1].strip()
            if "boundary=" in content_type
            else "frame"
        )
        jpeg = extract_first_jpeg(resp.iter_content(chunk_size=1024), boundary)
    return jpeg


def fetch_jpeg_via_save(base_url, timeout=30.0):
    """Ask the backend to capture a frame ({base}/image/save), return JPEG bytes."""
    base = base_url.rstrip("/")
    resp = requests.post(base + "/image/save", timeout=timeout)
    resp.raise_for_status()
    file_path = resp.json().get("file_path")
    if not file_path:
        raise RuntimeError("Backend did not return a file_path")
    name = os.path.basename(file_path)
    resp = requests.get(base + "/saved_images/" + name, timeout=timeout)
    resp.raise_for_status()
    return resp.content


JPEG_FETCHERS = {
    "mjpeg": fetch_jpeg_from_mjpeg,
    "save": fetch_jpeg_via_save,
}
