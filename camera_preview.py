import json
import logging
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal


LOG = logging.getLogger("chuckles")
PREVIEW_TTL_SECONDS = 60


@dataclass(frozen=True)
class PreviewFrame:
    image: bytes = field(repr=False)
    captured_at: float
    captured_monotonic: float
    source: Literal["visitor", "manual"]
    version: int


class CameraPreview:
    def __init__(self, capture: Callable[[], bytes | None]) -> None:
        self.capture = capture
        self.capture_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.frame: PreviewFrame | None = None
        self.timer: threading.Timer | None = None
        self.version = 0
        self.error: str | None = None
        self.closed = False

    def _clear_locked(self) -> None:
        self.frame = None
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def clear(self) -> None:
        with self.state_lock:
            self._clear_locked()
            self.error = None

    def _expire(self) -> None:
        with self.state_lock:
            if (
                self.frame is not None
                and time.monotonic() - self.frame.captured_monotonic >= PREVIEW_TTL_SECONDS
            ):
                self._clear_locked()

    def current_frame(self) -> PreviewFrame | None:
        self._expire()
        with self.state_lock:
            return self.frame

    def status(self) -> dict[str, object]:
        frame = self.current_frame()
        with self.state_lock:
            error = self.error
        return {
            "available": frame is not None,
            "captured_at": frame.captured_at if frame else None,
            "source": frame.source if frame else None,
            "version": frame.version if frame else None,
            "remaining_seconds": max(
                0, PREVIEW_TTL_SECONDS - (time.monotonic() - frame.captured_monotonic)
            ) if frame else 0,
            "error": error,
        }

    def _capture(self, source: Literal["visitor", "manual"]) -> bytes | None:
        image = self.capture()
        with self.state_lock:
            self._clear_locked()
            if image is None:
                self.error = "Camera capture failed. Check the speech process logs."
            elif not self.closed:
                self.error = None
                self.version += 1
                self.frame = PreviewFrame(
                    image, time.time(), time.monotonic(), source, self.version
                )
                self.timer = threading.Timer(PREVIEW_TTL_SECONDS, self._expire)
                self.timer.daemon = True
                self.timer.start()
        return image

    def capture_for_visitor(self) -> bytes | None:
        with self.capture_lock:
            return self._capture("visitor")

    def refresh(self) -> tuple[dict[str, object], int]:
        if not self.capture_lock.acquire(blocking=False):
            return {"error": "Camera busy with another capture. Try again shortly."}, 409
        try:
            image = self._capture("manual")
            return self.status(), 200 if image is not None else 503
        finally:
            self.capture_lock.release()

    def close(self) -> None:
        with self.state_lock:
            self.closed = True
            self._clear_locked()

    @contextmanager
    def serve(
        self, port: int = 8765, *,
        listening_status: Callable[[], dict[str, object]] | None = None,
        set_muted: Callable[[bool], dict[str, object]] | None = None,
        camera_enabled: bool = True,
    ) -> Generator[int, None, None]:
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def reply(
                self, status: int, body: bytes, content_type: str,
                frame: PreviewFrame | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Content-Type-Options", "nosniff")
                if frame is not None:
                    self.send_header("X-Preview-Captured-At", str(frame.captured_at))
                    self.send_header("X-Preview-Source", frame.source)
                    self.send_header("X-Preview-Version", str(frame.version))
                    self.send_header("X-Preview-Remaining", str(max(
                        0, PREVIEW_TTL_SECONDS - (time.monotonic() - frame.captured_monotonic)
                    )))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    LOG.debug("Camera preview client disconnected.")

            def reply_json(self, status: int, payload: dict[str, object]) -> None:
                self.reply(status, json.dumps(payload).encode("utf-8"), "application/json")

            def do_GET(self) -> None:
                if self.path == "/listening/status":
                    if listening_status is None:
                        self.reply_json(503, {"error": "Listening controls unavailable."})
                    else:
                        self.reply_json(200, listening_status())
                elif self.path in ("/status", "/image") and not camera_enabled:
                    self.reply_json(503, {"error": "Camera preview is disabled."})
                elif self.path == "/status":
                    self.reply_json(200, preview.status())
                elif self.path == "/image":
                    frame = preview.current_frame()
                    if frame is None:
                        self.reply_json(404, {"error": "No current camera snapshot."})
                    else:
                        self.reply(200, frame.image, "image/jpeg", frame)
                else:
                    self.reply_json(404, {"error": "Unknown preview endpoint."})

            def do_POST(self) -> None:
                if self.path not in ("/refresh", "/listening/mute", "/listening/unmute"):
                    self.reply_json(404, {"error": "Unknown preview endpoint."})
                elif self.headers.get("X-Camera-Preview") != "1":
                    self.reply_json(403, {"error": "Preview request header required."})
                elif self.path.startswith("/listening/"):
                    if set_muted is None:
                        self.reply_json(503, {"error": "Listening controls unavailable."})
                    else:
                        try:
                            state = set_muted(self.path == "/listening/mute")
                        except TimeoutError as error:
                            LOG.warning("Listening control failed: %s", error)
                            self.reply_json(503, {"error": str(error)})
                        else:
                            self.reply_json(200, state)
                elif not camera_enabled:
                    self.reply_json(503, {"error": "Camera preview is disabled."})
                else:
                    payload, status = preview.refresh()
                    self.reply_json(status, payload)

            def log_message(self, format: str, *args: object) -> None:
                LOG.debug("Camera preview HTTP: %s", format % args)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        LOG.info("Chuckles controls listening on loopback port %s.", server.server_port)
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            self.close()
