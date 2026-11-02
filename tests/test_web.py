import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from camera_preview import CameraPreview


SCRIPT = Path(__file__).resolve().parents[1] / "chuckles24-web1.py"
SPEC = importlib.util.spec_from_file_location("chuckles_web", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
web = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = web
gpio = Mock()
gpio.Chip.return_value.get_line.side_effect = {pin: Mock() for pin in (7, 8, 9)}.get
with (
    patch.dict(sys.modules, {"gpiod": gpio}),
    patch.dict(os.environ, {"CAMERA_PREVIEW_PORT": "8765"}),
    patch("dotenv.load_dotenv"),
):
    SPEC.loader.exec_module(web)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()

    def assert_no_cache(self, response):
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_phone_page_keeps_effect_forms_and_includes_preview(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for path in ("/firebutton", "/smokebutton", "/hornbutton", "/donotpressbutton"):
            self.assertIn(f'action="{path}"', page)
        self.assertIn('id="camera-preview"', page)
        self.assertIn('type="button" id="camera-refresh"', page)
        self.assertIn("/static/camera-preview.js", page)
        self.assertIn("No password", page)
        self.assertIn('id="listening-toggle"', page)
        self.assertIn('data-mute-url="/listening/mute"', page)
        self.assertIn('data-unmute-url="/listening/unmute"', page)
        with self.client.get("/static/camera-preview.js") as script:
            self.assertEqual(script.status_code, 200)

    def test_preview_http_proxy_round_trip_without_password(self):
        capture = Mock(return_value=b"jpeg")
        preview = CameraPreview(capture)
        with preview.serve(0) as port, patch.object(web, "preview_port", port):
            status = self.client.get("/camera/status")
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json["available"])
            self.assert_no_cache(status)
            missing = self.client.get("/camera/image")
            self.assertEqual(missing.status_code, 404)
            self.assert_no_cache(missing)
            forbidden = self.client.post("/camera/refresh")
            self.assertEqual(forbidden.status_code, 403)
            capture.assert_not_called()
            refresh = self.client.post(
                "/camera/refresh", headers={"X-Camera-Preview": "1"}
            )
            self.assertEqual(refresh.status_code, 200)
            self.assertEqual(refresh.json["source"], "manual")
            self.assert_no_cache(refresh)
            image = self.client.get("/camera/image")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.data, b"jpeg")
            self.assertEqual(image.headers["Content-Type"], "image/jpeg")
            self.assertEqual(image.headers["X-Preview-Source"], "manual")
            self.assert_no_cache(image)
            capture.assert_called_once()
            preview.clear()
            self.assertEqual(self.client.get("/camera/image").status_code, 404)

    def test_preview_unavailable_is_an_explicit_non_cached_error(self):
        with (
            patch.object(web, "urlopen", side_effect=URLError("not running")),
            self.assertLogs(web.app.logger, level="WARNING"),
        ):
            response = self.client.get("/camera/status")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Start the speech process", response.json["error"])
        self.assert_no_cache(response)

    def test_listening_controls_work_even_when_camera_is_disabled(self):
        capture = Mock(return_value=b"jpeg")
        preview = CameraPreview(capture)
        state = {"muted": False, "recognition_stopped": True}

        def set_muted(muted):
            state["muted"] = muted
            return state.copy()

        with preview.serve(
            0, listening_status=state.copy, set_muted=set_muted, camera_enabled=False,
        ) as port, patch.object(web, "preview_port", port):
            response = self.client.get("/listening/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, state)
            self.assert_no_cache(response)
            forbidden = self.client.post("/listening/mute")
            self.assertEqual(forbidden.status_code, 403)
            self.assertFalse(state["muted"])
            for action, expected in (("mute", True), ("mute", True), ("unmute", False)):
                response = self.client.post(
                    f"/listening/{action}", headers={"X-Camera-Preview": "1"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["muted"], expected)
                self.assertTrue(response.json["recognition_stopped"])
                self.assert_no_cache(response)
            for path in ("/camera/status", "/camera/image"):
                self.assertEqual(self.client.get(path).status_code, 503)
            self.assertEqual(self.client.post(
                "/camera/refresh", headers={"X-Camera-Preview": "1"}
            ).status_code, 503)
            self.assertEqual(self.client.post(
                "/listening/invalid", headers={"X-Camera-Preview": "1"}
            ).status_code, 404)
            capture.assert_not_called()

    def test_listening_stop_timeout_is_forwarded_without_success(self):
        preview = CameraPreview(Mock())
        with preview.serve(
            0, listening_status=lambda: {"muted": True, "recognition_stopped": False},
            set_muted=Mock(side_effect=TimeoutError("Recognition stop not confirmed")),
        ) as port, patch.object(web, "preview_port", port):
            with self.assertLogs("chuckles", level="WARNING"):
                response = self.client.post(
                    "/listening/mute", headers={"X-Camera-Preview": "1"}
                )
            self.assertEqual(response.status_code, 503)
            self.assertIn("not confirmed", response.json["error"])
            self.assert_no_cache(response)

    def test_listening_status_is_unavailable_without_service_or_callbacks(self):
        with (
            patch.object(web, "urlopen", side_effect=URLError("not running")),
            self.assertLogs(web.app.logger, level="WARNING"),
        ):
            response = self.client.get("/listening/status")
        self.assertEqual(response.status_code, 503)
        self.assertIn("updated speech process", response.json["error"])
        self.assert_no_cache(response)
        preview = CameraPreview(Mock())
        with preview.serve(0) as port, patch.object(web, "preview_port", port):
            self.assertEqual(self.client.get("/listening/status").status_code, 503)
            self.assertEqual(self.client.post(
                "/listening/mute", headers={"X-Camera-Preview": "1"}
            ).status_code, 503)

    def test_effect_durations_and_no_op_button_are_unchanged(self):
        for path, line, duration in (
            ("/firebutton", web.fire_line, 1),
            ("/smokebutton", web.smoke_line, 4),
            ("/hornbutton", web.horn_line, 1.5),
        ):
            with self.subTest(path=path):
                line.reset_mock()
                with patch.object(web.time, "sleep") as sleep:
                    self.assertEqual(self.client.post(path).status_code, 200)
                self.assertEqual(
                    [call.args for call in line.set_value.call_args_list], [(1,), (0,)]
                )
                sleep.assert_called_once_with(duration)
        with patch.object(web.time, "sleep") as sleep:
            self.assertEqual(self.client.post("/donotpressbutton").status_code, 200)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
