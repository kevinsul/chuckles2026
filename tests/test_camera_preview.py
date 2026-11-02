import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import camera_preview


class PreviewTests(unittest.TestCase):
    def preview(self, capture=None):
        preview = camera_preview.CameraPreview(capture or Mock(return_value=b"jpeg"))
        self.addCleanup(preview.close)
        return preview

    def test_only_latest_image_is_retained_and_source_is_explicit(self):
        capture = Mock(side_effect=[b"visitor-image", b"manual-image"])
        preview = self.preview(capture)
        self.assertFalse(preview.status()["available"])
        self.assertEqual(preview.capture_for_visitor(), b"visitor-image")
        previous_timer = preview.timer
        self.assertEqual(preview.current_frame().source, "visitor")
        self.assertEqual(preview.current_frame().version, 1)
        state, status = preview.refresh()
        self.assertEqual(status, 200)
        self.assertEqual(state["source"], "manual")
        self.assertEqual(state["version"], 2)
        self.assertTrue(previous_timer.finished.is_set())
        self.assertEqual(preview.current_frame().image, b"manual-image")
        self.assertNotIn("manual-image", repr(preview.current_frame()))
        self.assertNotIn("manual-image", json.dumps(state))

    def test_exact_expiry_threshold(self):
        preview = self.preview()
        with patch.object(camera_preview.time, "monotonic", return_value=100):
            preview.capture_for_visitor()
        with patch.object(camera_preview.time, "monotonic", return_value=159.99):
            self.assertTrue(preview.status()["available"])
        with patch.object(camera_preview.time, "monotonic", return_value=160):
            self.assertIsNone(preview.current_frame())
            self.assertFalse(preview.status()["available"])
        self.assertIsNone(preview.frame)
        self.assertIsNone(preview.timer)

    def test_timer_removes_image_without_any_client_request(self):
        preview = self.preview()
        with patch.object(camera_preview, "PREVIEW_TTL_SECONDS", 0.02):
            preview.capture_for_visitor()
            timer = preview.timer
            timer.join(timeout=1)
        self.assertFalse(timer.is_alive())
        self.assertIsNone(preview.frame)

    def test_failure_removes_previous_preview(self):
        preview = self.preview(Mock(side_effect=[b"previous-image", None]))
        preview.capture_for_visitor()
        state, status = preview.refresh()
        self.assertEqual(status, 503)
        self.assertFalse(state["available"])
        self.assertIn("failed", state["error"])
        self.assertIsNone(preview.frame)

    def test_close_cancels_timer_and_prevents_late_capture_retention(self):
        preview = self.preview()
        preview.capture_for_visitor()
        timer = preview.timer
        preview.close()
        self.assertTrue(timer.finished.is_set())
        self.assertIsNone(preview.frame)
        preview.capture_for_visitor()
        self.assertIsNone(preview.frame)
        self.assertIsNone(preview.timer)

    def test_manual_capture_is_rejected_when_visitor_capture_is_busy(self):
        started = threading.Event()
        release = threading.Event()

        def capture():
            started.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release capture")
            return b"visitor"

        preview = self.preview(capture)
        worker = threading.Thread(target=preview.capture_for_visitor)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            state, status = preview.refresh()
            self.assertEqual(status, 409)
            self.assertIn("busy", state["error"])
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_visitor_waits_for_manual_capture_without_concurrent_camera_access(self):
        first_started = threading.Event()
        visitor_started = threading.Event()
        release = threading.Event()
        calls = []

        def capture():
            calls.append(threading.current_thread().name)
            if len(calls) == 1:
                first_started.set()
                if not release.wait(2):
                    raise TimeoutError("Test did not release capture")
            return b"image"

        preview = self.preview(capture)
        manual = threading.Thread(target=preview.refresh, name="manual-test")

        def visitor_capture():
            visitor_started.set()
            preview.capture_for_visitor()

        visitor = threading.Thread(target=visitor_capture, name="visitor-test")
        manual.start()
        try:
            self.assertTrue(first_started.wait(1))
            visitor.start()
            self.assertTrue(visitor_started.wait(1))
            self.assertEqual(calls, ["manual-test"])
        finally:
            release.set()
            manual.join(timeout=2)
            visitor.join(timeout=2)
        self.assertEqual(calls, ["manual-test", "visitor-test"])
        self.assertEqual(preview.current_frame().source, "visitor")

    def test_loopback_service_and_shutdown(self):
        capture = Mock(return_value=b"jpeg")
        preview = self.preview(capture)
        with preview.serve(0) as port:
            base = f"http://127.0.0.1:{port}"
            with urlopen(base + "/status", timeout=2) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertFalse(json.load(response)["available"])
            capture.assert_not_called()
            with self.assertRaises(HTTPError) as missing:
                urlopen(base + "/image", timeout=2)
            self.assertEqual(missing.exception.code, 404)
            missing.exception.close()
            with self.assertRaises(HTTPError) as forbidden:
                urlopen(Request(base + "/refresh", method="POST"), timeout=2)
            self.assertEqual(forbidden.exception.code, 403)
            forbidden.exception.close()
            capture.assert_not_called()
            with urlopen(Request(
                base + "/refresh", method="POST", headers={"X-Camera-Preview": "1"}
            ), timeout=2) as response:
                self.assertEqual(json.load(response)["source"], "manual")
            with urlopen(base + "/image", timeout=2) as response:
                self.assertEqual(response.read(), b"jpeg")
                self.assertEqual(response.headers["X-Preview-Source"], "manual")
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertGreater(float(response.headers["X-Preview-Remaining"]), 0)
            with self.assertRaises(HTTPError) as unknown:
                urlopen(base + "/unknown", timeout=2)
            self.assertEqual(unknown.exception.code, 404)
            unknown.exception.close()
        self.assertIsNone(preview.frame)
        with self.assertRaises(URLError):
            urlopen(base + "/status", timeout=1)


if __name__ == "__main__":
    unittest.main()
