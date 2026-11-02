import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import httpx


SCRIPT = Path(__file__).resolve().parents[1] / "chuckles24-speech2.py"
SPEC = importlib.util.spec_from_file_location("chuckles_speech", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
speech = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = speech
SPEC.loader.exec_module(speech)


def settings():
    return speech.Settings(
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        deployment="chuckles-chat",
        speech_key="test-speech-key",
        speech_region="eastus",
        camera_enabled=False,
        camera_preview_enabled=False,
        camera_preview_port=0,
    )


def event(text):
    return SimpleNamespace(result=SimpleNamespace(
        text=text, reason=speech.speechsdk.ResultReason.RecognizedSpeech
    ))


def completed():
    return SimpleNamespace(
        reason=speech.speechsdk.ResultReason.SynthesizingAudioCompleted
    )


class SpeechTests(unittest.TestCase):
    def conversation(self):
        recognizer = Mock()
        for name in ("recognized", "canceled", "session_stopped", "recognizing"):
            setattr(recognizer, name, Mock(spec_set=speech.speechsdk.EventSignal))
        conv = speech.Conversation(Mock(), settings(), recognizer, Mock(), Mock())
        # Existing lifecycle tests isolate recognition from wake-name policy.
        conv.accepts_prompt = Mock(return_value=True)
        return conv

    def test_wake_name_whole_word_and_operator_command_policy(self):
        conv = self.conversation()
        accepts = speech.Conversation.accepts_prompt
        for text in ("Chuckles", "Hey CHUCKLES!", "Hello, Chuckles, how are you?",
                     "quit!", "New Visitors."):
            self.assertTrue(accepts(conv, text), text)
        for text in ("Hello", "chucklesome", "superchuckles", "Who is there?"):
            self.assertFalse(accepts(conv, text), text)
        conv.last_visitor_at = 100
        self.assertTrue(accepts(conv, "And what about my hat?"))
        conv.expire_visitors(145)
        self.assertFalse(accepts(conv, "And what about my hat?"))

    def test_wake_conversation_ignores_background_and_expires_at_45_seconds(self):
        conv = self.conversation()
        conv.accepts_prompt = speech.Conversation.accepts_prompt.__get__(conv)
        conv.heard.get = Mock(side_effect=[
            speech.Heard(text="Background conversation", recognized_at=10),
            speech.Heard(text="Hey Chuckles", recognized_at=20),
            speech.Heard(text="My costume is homemade", recognized_at=64.99),
            speech.Heard(text="Ignored after timeout", recognized_at=109.99),
            speech.Heard(text="Still ignored", recognized_at=120),
            speech.Heard(text="Chuckles, are you there?", recognized_at=140),
            speech.Heard(text="new visitors", recognized_at=141),
            speech.Heard(text="Ignored after reset", recognized_at=142),
            speech.Heard(text="quit", recognized_at=143),
        ])
        snapshots = []

        def answer(client, config, messages):
            snapshots.append(list(messages))
            return "A short reply"

        conv.sayit = Mock()
        with (
            patch.object(speech, "send_message", side_effect=answer),
            patch.object(speech.time, "sleep"),
            patch.object(speech, "capture_snapshot") as capture,
        ):
            conv.run()
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0][-1]["content"], "Hey Chuckles")
        self.assertEqual(len(snapshots[1]), 4)
        self.assertEqual(snapshots[2][-1]["content"], "Chuckles, are you there?")
        self.assertEqual(len(snapshots[2]), 2)
        self.assertEqual(len(conv.messages), 1)
        capture.assert_not_called()
        self.assertEqual(conv.sayit.call_count, 3)

    def background_listener(self, conv):
        executor = ThreadPoolExecutor(max_workers=1)

        def cleanup():
            conv.heard.put(speech.Heard(error="Test listener cleanup"))
            executor.shutdown(wait=True)

        self.addCleanup(cleanup)
        return executor.submit(conv.listen)

    def test_mute_stops_recognition_and_unmute_rejects_old_session_callbacks(self):
        conv = self.conversation()
        started = threading.Event()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = started.set
        future = self.background_listener(conv)
        self.assertTrue(started.wait(1))
        old_recognized = conv.recognizer.recognized.connect.call_args.args[0]
        old_canceled = conv.recognizer.canceled.connect.call_args.args[0]
        old_stopped = conv.recognizer.session_stopped.connect.call_args.args[0]
        self.assertEqual(conv.set_muted(True), {"muted": True, "recognition_stopped": True})
        conv.recognizer.stop_continuous_recognition_async.assert_called_once()
        conv.recognized(event("Ignored while muted"))
        self.assertTrue(conv.heard.empty())
        self.assertFalse(future.done())
        started.clear()
        self.assertFalse(conv.set_muted(False)["muted"])
        self.assertTrue(started.wait(1))
        old_recognized(event("Stale speech from before mute"))
        old_canceled(SimpleNamespace(reason="Old error", error_details="old session"))
        old_stopped(SimpleNamespace())
        self.assertTrue(conv.heard.empty())
        self.assertTrue(conv.listening)
        conv.recognizer.recognized.connect.call_args.args[0](event("Fresh speech"))
        self.assertEqual(future.result(timeout=1), "Fresh speech")
        self.assertEqual(conv.recognizer.start_continuous_recognition_async.call_count, 2)
        self.assertEqual(conv.recognizer.stop_continuous_recognition_async.call_count, 2)
        for signal in (
            conv.recognizer.recognized, conv.recognizer.canceled,
            conv.recognizer.session_stopped,
        ):
            self.assertEqual(signal.disconnect_all.call_count, 2)
        conv.client.chat.completions.create.assert_not_called()
        conv.synthesizer.speak_ssml_async.assert_not_called()

    def test_mute_is_not_confirmed_until_sdk_stop_completes(self):
        conv = self.conversation()
        started = threading.Event()
        stopping = threading.Event()
        release = threading.Event()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = started.set

        def stop():
            stopping.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release SDK stop")

        conv.recognizer.stop_continuous_recognition_async.return_value.get.side_effect = stop
        future = self.background_listener(conv)
        self.assertTrue(started.wait(1))
        with ThreadPoolExecutor(max_workers=1) as controller:
            muted = controller.submit(conv.set_muted, True)
            try:
                self.assertTrue(stopping.wait(1))
                self.assertFalse(muted.done())
                self.assertEqual(
                    conv.listening_status(), {"muted": True, "recognition_stopped": False}
                )
            finally:
                release.set()
            self.assertTrue(muted.result(timeout=1)["recognition_stopped"])
        self.assertFalse(future.done())

    def test_mute_timeout_reports_uncertainty_not_success(self):
        conv = self.conversation()
        conv.recognition_stopped.clear()
        with patch.object(speech, "MUTE_CONFIRM_SECONDS", 0.01):
            with self.assertRaisesRegex(TimeoutError, "not confirmed stopping"):
                conv.set_muted(True)
        self.assertEqual(
            conv.listening_status(), {"muted": True, "recognition_stopped": False}
        )

    def test_sdk_stop_failure_during_mute_surfaces_without_confirmation(self):
        conv = self.conversation()
        started = threading.Event()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = started.set
        conv.recognizer.stop_continuous_recognition_async.return_value.get.side_effect = (
            RuntimeError("SDK stop failed")
        )
        future = self.background_listener(conv)
        self.assertTrue(started.wait(1))
        with patch.object(speech, "MUTE_CONFIRM_SECONDS", 0.3):
            with self.assertRaises(TimeoutError):
                conv.set_muted(True)
        with self.assertRaisesRegex(RuntimeError, "SDK stop failed"):
            future.result(timeout=1)
        self.assertFalse(conv.recognition_stopped.is_set())
        conv.recognizer.stop_continuous_recognition_async.assert_called_once()

    def test_muted_listener_does_not_start_capture_and_still_expires_history(self):
        conv = self.conversation()
        conv.messages.append({"role": "user", "content": "Previous visitor"})
        conv.last_visitor_at = 100
        conv.set_muted(True)
        conv.heard.get = Mock(side_effect=[speech.Empty(), KeyboardInterrupt()])
        with patch.object(speech.time, "monotonic", return_value=190):
            with self.assertRaises(KeyboardInterrupt):
                conv.listen()
        self.assertEqual(len(conv.messages), 1)
        conv.recognizer.start_continuous_recognition_async.assert_not_called()
        conv.recognizer.stop_continuous_recognition_async.assert_not_called()

    def test_pending_speech_is_discarded_and_stale_dequeued_speech_is_not_replayed(self):
        conv = self.conversation()
        conv.messages.append({"role": "user", "content": "Current visitor"})
        conv.listening = True
        conv.recognized(event("Queued before mute"))
        conv.set_muted(True)
        self.assertTrue(conv.heard.empty())
        conv.set_muted(False)
        conv.heard.get = Mock(side_effect=[
            speech.Heard(text="Already dequeued old speech", generation=0),
            speech.Heard(text="Fresh speech", generation=1),
        ])
        self.assertEqual(conv.listen(), "Fresh speech")
        self.assertEqual(conv.messages[-1]["content"], "Current visitor")

    def test_muting_does_not_silently_discard_a_queued_recognition_error(self):
        conv = self.conversation()
        conv.listening = True
        conv.fail("Actual recognition failure")
        conv.set_muted(True)
        with self.assertRaisesRegex(RuntimeError, "Actual recognition failure"):
            conv.listen()
        conv.recognizer.start_continuous_recognition_async.assert_not_called()

    def test_reply_already_in_progress_finishes_and_restart_is_unmuted(self):
        conv = self.conversation()
        self.prompts(conv, ["Hello", "quit"])

        def answer(*args):
            self.assertTrue(conv.recognition_stopped.is_set())
            self.assertTrue(conv.set_muted(True)["muted"])
            return "The current reply can finish"

        def playback(text):
            self.assertEqual(text, "The current reply can finish")
            self.assertTrue(conv.muted)
            conv.set_muted(False)

        conv.sayit = Mock(side_effect=playback)
        with (
            patch.object(speech, "send_message", side_effect=answer) as llm,
            patch.object(speech.time, "sleep"),
        ):
            conv.run()
        llm.assert_called_once()
        conv.sayit.assert_called_once()
        self.assertFalse(self.conversation().listening_status()["muted"])

    def test_snapshot_uses_stdout_without_saving_a_file(self):
        jpeg = b"\xff\xd8\xfftest-image\xff\xd9"
        with patch.object(speech.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout=jpeg
        )) as capture:
            self.assertEqual(speech.capture_snapshot(settings()), jpeg)
        capture.assert_called_once_with(
            [
                "fswebcam", "--device", "/dev/video0", "--resolution", "1280x720",
                "--no-banner", "--skip", "10", "--jpeg", "85", "-",
            ],
            capture_output=True, timeout=8, check=False,
        )

    def test_capture_failures_warn_without_logging_image_bytes(self):
        for result in (
            FileNotFoundError("missing"),
            PermissionError("denied"),
            speech.subprocess.TimeoutExpired("fswebcam", 8),
            SimpleNamespace(returncode=1, stdout=b"private-image-data"),
            SimpleNamespace(returncode=0, stdout=b""),
            SimpleNamespace(returncode=0, stdout=b"private-image-data"),
            SimpleNamespace(returncode=0, stdout=b"\xff\xd8\xfftruncated"),
        ):
            with self.subTest(result=result):
                kwargs = (
                    {"side_effect": result} if isinstance(result, Exception)
                    else {"return_value": result}
                )
                with (
                    patch.object(speech.subprocess, "run", **kwargs),
                    self.assertLogs("chuckles", level="WARNING") as logs,
                ):
                    self.assertIsNone(speech.capture_snapshot(settings()))
                self.assertIn("audio only", "\n".join(logs.output))
                self.assertNotIn("private-image-data", "\n".join(logs.output))

    def test_webcam_requests_have_only_current_image_and_history_stays_text_only(self):
        requests = []
        images = [b"\xff\xd8\xffimage-one\xff\xd9", b"\xff\xd8\xffimage-two\xff\xd9"]

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "chatcmpl-test", "created": 0, "model": "test",
                "object": "chat.completion",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Nice cape, visitor!"},
                }],
            })

        config = speech.Settings(**{**settings().__dict__, "camera_enabled": True})
        with speech.AzureOpenAI(
            azure_endpoint=config.endpoint, api_key=config.api_key,
            api_version=config.api_version,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as client:
            conv = speech.Conversation(client, config, Mock(), Mock(), Mock())
            conv.accepts_prompt = Mock(return_value=True)
            self.prompts(conv, ["Hello", "What about my hat?", "new visitors", "Hi", "quit"])
            conv.sayit = Mock()
            captures = iter([
                SimpleNamespace(returncode=0, stdout=images[0]),
                SimpleNamespace(returncode=0, stdout=images[1]),
                SimpleNamespace(returncode=1, stdout=b""),
            ])

            def capture(*args, **kwargs):
                self.assertFalse(conv.listening)
                self.assertGreater(
                    conv.recognizer.stop_continuous_recognition_async.call_count, 0
                )
                return next(captures)

            with (
                patch.object(speech.subprocess, "run", side_effect=capture) as camera,
                patch.object(speech.time, "sleep"),
                self.assertLogs("chuckles", level="INFO") as logs,
            ):
                conv.run()
        self.assertEqual(camera.call_count, 3)
        self.assertEqual(conv.sayit.call_count, 3)
        for index in (0, 1):
            messages = requests[index]["messages"]
            parts = messages[-1]["content"]
            self.assertEqual(parts[0]["text"], ["Hello", "What about my hat?"][index])
            self.assertEqual(
                parts[1]["image_url"]["url"],
                "data:image/jpeg;base64," + base64.b64encode(images[index]).decode("ascii"),
            )
            self.assertEqual(parts[1]["image_url"]["detail"], "auto")
            self.assertEqual(sum(isinstance(msg["content"], list) for msg in messages), 1)
        self.assertEqual(requests[1]["messages"][-2]["content"], "Nice cape, visitor!")
        failed_messages = requests[2]["messages"]
        self.assertIn("No current image", failed_messages[1]["content"])
        self.assertEqual(failed_messages[-1], {"role": "user", "content": "Hi"})
        self.assertEqual(len(failed_messages), 3)
        self.assertTrue(all(isinstance(msg["content"], str) for msg in failed_messages))
        self.assertTrue(all(isinstance(msg["content"], str) for msg in conv.messages))
        self.assertEqual(len(conv.messages), 3)
        for image in images:
            self.assertNotIn(base64.b64encode(image).decode("ascii"), "\n".join(logs.output))
        self.assertNotIn("Nice cape, visitor!", "\n".join(logs.output))

    def test_camera_disabled_does_not_capture_or_attach_visual_state(self):
        conv = self.conversation()
        self.prompts(conv, ["Hello", "quit"])
        conv.sayit = Mock()
        with (
            patch.object(speech.subprocess, "run") as capture,
            patch.object(speech, "send_message", return_value="Hello") as llm,
            patch.object(speech.time, "sleep"),
        ):
            conv.run()
        capture.assert_not_called()
        self.assertEqual(llm.call_args.args[2], conv.messages)
        self.assertTrue(all(isinstance(msg["content"], str) for msg in conv.messages))

    def test_camera_settings_defaults_overrides_and_validation(self):
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test", "AZURE_OPENAI_DEPLOYMENT": "test",
            "AZURE_SPEECH_KEY": "test", "AZURE_SPEECH_REGION": "eastus",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(speech.Settings.from_env().camera_enabled)
            self.assertEqual(speech.Settings.from_env().camera_device, "/dev/video0")
            os.environ["CAMERA_ENABLED"] = "false"
            os.environ["CAMERA_DEVICE"] = "/dev/video2"
            self.assertFalse(speech.Settings.from_env().camera_enabled)
            self.assertEqual(speech.Settings.from_env().camera_device, "/dev/video2")
            os.environ["CAMERA_ENABLED"] = "maybe"
            with self.assertRaisesRegex(ValueError, "CAMERA_ENABLED"):
                speech.Settings.from_env()
            os.environ["CAMERA_ENABLED"] = "true"
            for value in ("", "  ", "--bad-option"):
                os.environ["CAMERA_DEVICE"] = value
                with self.assertRaisesRegex(ValueError, "CAMERA_DEVICE"):
                    speech.Settings.from_env()

    def test_preview_settings_defaults_and_validation(self):
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test", "AZURE_OPENAI_DEPLOYMENT": "test",
            "AZURE_SPEECH_KEY": "test", "AZURE_SPEECH_REGION": "eastus",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(speech.Settings.from_env().camera_preview_enabled)
            self.assertEqual(speech.Settings.from_env().camera_preview_port, 8765)
            os.environ["CAMERA_PREVIEW_ENABLED"] = "false"
            os.environ["CAMERA_PREVIEW_PORT"] = "9876"
            self.assertFalse(speech.Settings.from_env().camera_preview_enabled)
            self.assertEqual(speech.Settings.from_env().camera_preview_port, 9876)
            os.environ["CAMERA_PREVIEW_ENABLED"] = "maybe"
            with self.assertRaisesRegex(ValueError, "CAMERA_PREVIEW_ENABLED"):
                speech.Settings.from_env()
            os.environ["CAMERA_PREVIEW_ENABLED"] = "true"
            for value in ("0", "65536", "bad"):
                os.environ["CAMERA_PREVIEW_PORT"] = value
                with self.assertRaises(ValueError):
                    speech.Settings.from_env()

    def test_visitor_snapshot_is_reused_by_preview_and_reset_clears_it(self):
        conv = self.conversation()
        conv.settings = speech.Settings(**{
            **settings().__dict__, "camera_enabled": True, "camera_preview_enabled": True,
        })
        conv.preview = speech.CameraPreview(lambda: speech.capture_snapshot(conv.settings))
        self.addCleanup(conv.preview.close)
        conv.messages.append({"role": "user", "content": "Hello"})
        with (
            patch.object(speech, "capture_snapshot", return_value=b"current-image") as capture,
            patch.object(speech, "send_message", return_value="Nice hat") as llm,
        ):
            self.assertEqual(conv.respond("Hello"), "Nice hat")
        capture.assert_called_once()
        self.assertEqual(conv.preview.current_frame().image, b"current-image")
        self.assertEqual(conv.preview.current_frame().source, "visitor")
        self.assertIn(
            base64.b64encode(b"current-image").decode("ascii"),
            llm.call_args.args[2][-1]["content"][1]["image_url"]["url"],
        )
        conv.reset_visitors()
        self.assertIsNone(conv.preview.current_frame())
        self.assertEqual(len(conv.messages), 1)

    def test_grounding_is_loaded_from_adjacent_file_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                conv = self.conversation()
            finally:
                os.chdir(previous)
        prompt = conv.messages[0]["content"]
        self.assertIn(speech.SYSTEM_PROMPT, prompt)
        self.assertIn(speech.GROUNDING_PATH.read_text(encoding="utf-8").strip(), prompt)
        for fact in ("Halloween 2015", "Kevin Sullivan", "Neighbor One", "Neighbor Two",
                     "the host's home", "No additional local-news facts"):
            self.assertIn(fact, prompt)
        self.assertNotIn("washed-up former", prompt)
        self.assertNotIn("software engineer who", prompt)

    def test_grounding_requests_shorter_snark_and_occasional_joke_invitations(self):
        prompt = speech.load_grounding()
        self.assertIn("6-10 seconds", prompt)
        self.assertIn("never more than 22 words", prompt)
        self.assertIn("Ask for a joke only occasionally", prompt)
        self.assertIn("costume styling, makeup, accessories, or props", prompt)
        self.assertIn('mild language such as "hell" or "damn"', prompt)
        self.assertIn("not physical features or attractiveness", prompt)
        self.assertNotIn("at most 45 words", prompt)
        self.assertNotIn("usually 20-30 words", prompt)

    def test_missing_or_empty_grounding_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grounding.txt"
            with patch.object(speech, "GROUNDING_PATH", path):
                with self.assertRaises(FileNotFoundError):
                    self.conversation()
                path.write_text(" \n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Grounding file is empty"):
                    self.conversation()
                path.write_text("Approved test lore", encoding="utf-8")
                self.assertIn("Approved test lore", self.conversation().messages[0]["content"])

    def test_reset_command_clears_visitor_history_but_preserves_grounding(self):
        for command in ("new visitors", "New Visitors.", " NEW VISITORS! "):
            with self.subTest(command=command):
                conv = self.conversation()
                grounding = conv.messages[0]
                self.prompts(conv, ["My name is Test Visitor", command, "Hello", "quit"])
                snapshots = []

                def answer(client, config, messages):
                    snapshots.append(list(messages))
                    return "A fresh joke"

                conv.sayit = Mock()
                with (
                    patch.object(speech, "send_message", side_effect=answer),
                    patch.object(speech.time, "sleep"),
                    self.assertLogs("chuckles", level="INFO") as logs,
                ):
                    conv.run()
                self.assertEqual(len(snapshots), 2)
                self.assertEqual(snapshots[1], [grounding, {"role": "user", "content": "Hello"}])
                self.assertEqual(conv.sayit.call_count, 2)
                self.assertIn("Visitor session cleared.", "\n".join(logs.output))
                self.assertNotIn("Test Visitor", "\n".join(logs.output))
                self.assertNotIn("A fresh joke", "\n".join(logs.output))

    def test_reset_command_is_exact_not_a_substring(self):
        conv = self.conversation()
        self.prompts(conv, ["Are there new visitors?", "quit"])
        conv.sayit = Mock()
        with (
            patch.object(speech, "send_message", return_value="Perhaps") as llm,
            patch.object(speech.time, "sleep"),
        ):
            conv.run()
        llm.assert_called_once()

    def test_idle_boundary_resets_before_next_request(self):
        for gap in (44.99, 45.0, 45.01):
            with self.subTest(gap=gap):
                conv = self.conversation()
                turns = iter([("Remember my joke", 100.0), ("Hello", 100.0 + gap),
                              ("quit", 101.0 + gap)])

                def recognize():
                    text, now = next(turns)
                    with patch.object(speech.time, "monotonic", return_value=now):
                        conv.recognized(event(text))

                conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = recognize
                snapshots = []

                def answer(client, config, messages):
                    snapshots.append(list(messages))
                    return "Old joke verdict"

                conv.sayit = Mock()
                with (
                    patch.object(speech, "send_message", side_effect=answer),
                    patch.object(speech.time, "sleep"),
                ):
                    conv.run()
                self.assertEqual(len(snapshots[1]), 4 if gap < 45 else 2)
                self.assertEqual(snapshots[1][0], snapshots[0][0])
                if gap >= 45:
                    self.assertNotIn("Old joke verdict", str(snapshots[1]))
                    self.assertNotIn("Remember my joke", str(snapshots[1]))

    def test_idle_history_clears_while_waiting_even_without_new_speech(self):
        conv = self.conversation()
        grounding = conv.messages[0]
        conv.messages.extend([
            {"role": "user", "content": "Previous visitor"},
            {"role": "assistant", "content": "Previous reply"},
        ])
        conv.last_visitor_at = 100.0
        conv.heard.get = Mock(side_effect=[
            speech.Empty(), speech.Heard(text="Hello", recognized_at=200.0)
        ])
        with patch.object(speech.time, "monotonic", return_value=190.0):
            self.assertEqual(conv.listen(), "Hello")
        self.assertEqual(conv.messages, [grounding])
        self.assertEqual(conv.last_visitor_at, 200.0)

    def test_new_conversation_does_not_inherit_visitor_memory(self):
        previous = self.conversation()
        previous.messages.append({"role": "user", "content": "Private visitor detail"})
        fresh = self.conversation()
        self.assertEqual(fresh.messages, [previous.messages[0]])
        self.assertIsNone(fresh.last_visitor_at)

    def test_required_environment_and_secret_repr(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AZURE_OPENAI_ENDPOINT"):
                speech.Settings.from_env()
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "secret-llm",
            "AZURE_OPENAI_DEPLOYMENT": "my-deployment",
            "AZURE_SPEECH_KEY": "secret-speech",
            "AZURE_SPEECH_REGION": "eastus",
            "GPIO_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            config = speech.Settings.from_env()
            self.assertEqual(config.endpoint, env["AZURE_OPENAI_ENDPOINT"].rstrip("/"))
            self.assertEqual(config.deployment, "my-deployment")
            self.assertFalse(config.gpio_enabled)
            self.assertNotIn("secret-", repr(config))
            os.environ["GPIO_ENABLED"] = "typo"
            with self.assertRaisesRegex(ValueError, "GPIO_ENABLED"):
                speech.Settings.from_env()
            os.environ["GPIO_ENABLED"] = "true"
            os.environ["AZURE_OPENAI_ENDPOINT"] = "http://example.com"
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                speech.Settings.from_env()

    def test_ssml_escapes_model_output_and_attributes(self):
        config = speech.Settings(
            **{**settings().__dict__, "voice": 'a"b', "style": 'angry"'}
        )
        root = ElementTree.fromstring(speech.make_ssml("Frogs & <feet>!", config))
        self.assertEqual("".join(root.itertext()), "Frogs & <feet>!")
        voice = list(root)[0]
        self.assertEqual(voice.attrib["name"], 'a"b')
        self.assertEqual(list(voice)[0].attrib["style"], 'angry"')

    def test_ssml_uses_deeper_slower_stronger_angry_voice(self):
        root = ElementTree.fromstring(speech.make_ssml("Hello visitor.", settings()))
        voice = list(root)[0]
        self.assertEqual(voice.attrib["name"], "en-US-GuyNeural")
        expression = list(voice)[0]
        self.assertEqual(expression.attrib["style"], "angry")
        self.assertEqual(expression.attrib["styledegree"], "2.0")
        prosody = list(expression)[0]
        self.assertEqual(prosody.attrib, {"rate": "-5%", "pitch": "-25%"})

    def test_entra_settings_require_identity_not_resource_keys(self):
        env = {
            "AZURE_AUTH_MODE": "entra",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_DEPLOYMENT": "chuckles-chat",
            "AZURE_SPEECH_REGION": "eastus",
            "AZURE_SPEECH_ENDPOINT": "https://example.cognitiveservices.azure.com/",
            "AZURE_TENANT_ID": "test-tenant",
            "AZURE_CLIENT_ID": "test-app",
            "AZURE_CLIENT_SECRET": "secret-identity",
        }
        with patch.dict(os.environ, env, clear=True):
            config = speech.Settings.from_env()
            self.assertEqual(config.auth_mode, "entra")
            self.assertEqual(config.api_key, "")
            self.assertEqual(config.speech_key, "")
            self.assertNotIn("secret-identity", repr(config))
            os.environ["AZURE_SPEECH_ENDPOINT"] = "http://example.com"
            with self.assertRaisesRegex(ValueError, "HTTPS custom-domain"):
                speech.Settings.from_env()
            del os.environ["AZURE_CLIENT_SECRET"]
            with self.assertRaisesRegex(ValueError, "AZURE_CLIENT_SECRET"):
                speech.Settings.from_env()
            os.environ["AZURE_AUTH_MODE"] = "typo"
            with self.assertRaisesRegex(ValueError, "AZURE_AUTH_MODE"):
                speech.Settings.from_env()

    def test_ssml_style_can_be_disabled(self):
        config = speech.Settings(**{**settings().__dict__, "style": ""})
        self.assertNotIn("express-as", speech.make_ssml("Hello", config))

    def test_llm_uses_configured_deployment_and_chat_content(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" Frog hands! "))]
        )
        messages = [{"role": "user", "content": "Hello"}]
        self.assertEqual(speech.send_message(client, settings(), messages), "Frog hands!")
        client.chat.completions.create.assert_called_once_with(
            model="chuckles-chat", messages=messages, max_tokens=80, temperature=0.7
        )
        client.chat.completions.create.return_value.choices = []
        with self.assertRaisesRegex(RuntimeError, "no text"):
            speech.send_message(client, settings(), messages)
        client.chat.completions.create.return_value.choices = [
            SimpleNamespace(message=SimpleNamespace(content="   "))
        ]
        with self.assertRaisesRegex(RuntimeError, "no text"):
            speech.send_message(client, settings(), messages)

    def test_real_openai_sdk_constructs_azure_request_without_network(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "chatcmpl-test", "created": 0, "model": "test",
                "object": "chat.completion",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Frog hands!"},
                }],
            })

        config = settings()
        with speech.AzureOpenAI(
            azure_endpoint=config.endpoint, api_key=config.api_key,
            api_version=config.api_version,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as client:
            answer = speech.send_message(client, config, [{"role": "user", "content": "Hi"}])
        self.assertEqual(answer, "Frog hands!")
        self.assertEqual(requests[0].url.path,
                         "/openai/deployments/chuckles-chat/chat/completions")
        self.assertEqual(requests[0].url.params["api-version"], "2024-10-21")
        self.assertEqual(requests[0].headers["api-key"], "test-key")

    def prompts(self, conv, texts):
        turns = iter(texts)
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = (
            lambda: conv.recognized(event(next(turns)))
        )

    def test_only_first_final_prompt_is_accepted_and_late_callbacks_are_ignored(self):
        conv = self.conversation()
        conv.listening = True
        conv.recognized(event("What about your hands?"))
        conv.recognized(event("Speaker echo"))
        conv.canceled(SimpleNamespace(reason="EndOfStream", error_details=""))
        conv.session_stopped(SimpleNamespace())
        self.assertEqual(conv.heard.get_nowait().text, "What about your hands?")
        self.assertTrue(conv.heard.empty())
        self.assertFalse(conv.listening)

    def test_no_match_does_not_become_a_prompt(self):
        conv = self.conversation()
        conv.listening = True
        conv.recognized(SimpleNamespace(result=SimpleNamespace(
            text="noise", reason=speech.speechsdk.ResultReason.NoMatch
        )))
        self.assertTrue(conv.heard.empty())
        self.assertTrue(conv.listening)
        conv.recognized(event("   "))
        self.assertTrue(conv.heard.empty())
        conv.recognized(event("A valid question"))
        self.assertEqual(conv.heard.get_nowait().text, "A valid question")

    def test_playback_completion_and_motor_off(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.return_value.get.return_value = completed()
        conv.sayit("Hello")
        conv.rock_line.set_value.assert_any_call(1)
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))
        conv.synthesizer.stop_speaking_async.assert_called_once()

    def test_turn_order_stops_capture_before_llm_and_resumes_after_audio_settles(self):
        conv = self.conversation()
        self.prompts(conv, ["Hello", "quit"])
        phases = []

        def stop_capture():
            phases.append("stop")
            conv.session_stopped(SimpleNamespace())

        conv.recognizer.stop_continuous_recognition_async.return_value.get.side_effect = stop_capture

        def answer(*args):
            self.assertFalse(conv.listening)
            self.assertEqual(phases, ["stop"])
            conv.recognized(event("Ignored during LLM"))
            phases.append("llm")
            return "Hello visitor"

        def playback():
            self.assertFalse(conv.listening)
            self.assertEqual(conv.recognizer.start_continuous_recognition_async.call_count, 1)
            conv.recognized(event("Chuckles hears his own voice"))
            self.assertTrue(conv.heard.empty())
            phases.append("playback complete")
            return completed()

        conv.synthesizer.speak_ssml_async.return_value.get.side_effect = playback

        def settle(seconds):
            self.assertEqual(seconds, 0.75)
            self.assertFalse(conv.listening)
            self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))
            conv.recognized(event("Residual Bluetooth audio"))
            self.assertTrue(conv.heard.empty())
            phases.append("settle")

        with (
            patch.object(speech, "send_message", side_effect=answer) as llm,
            patch.object(speech.time, "sleep", side_effect=settle),
        ):
            conv.run()
        self.assertEqual(phases, ["stop", "llm", "playback complete", "settle", "stop"])
        llm.assert_called_once()
        self.assertEqual(conv.recognizer.start_continuous_recognition_async.call_count, 2)
        conv.synthesizer.speak_ssml_async.assert_called_once()
        conv.recognizer.recognizing.connect.assert_not_called()

    def test_synthesis_failure_surfaces_and_stops_motor(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.return_value.get.return_value = SimpleNamespace(
            reason=speech.speechsdk.ResultReason.Canceled, cancellation_details="bad key"
        )
        with self.assertRaisesRegex(RuntimeError, "bad key"):
            conv.sayit("Hello")
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_synthesis_start_exception_stops_motor(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.side_effect = RuntimeError("speaker unavailable")
        with self.assertRaisesRegex(RuntimeError, "speaker unavailable"):
            conv.sayit("Hello")
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_worker_timeout_exception_does_not_poll_forever(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.return_value.get.side_effect = TimeoutError("SDK")
        with self.assertRaisesRegex(TimeoutError, "SDK"):
            conv.sayit("Hello")

    def test_ctrl_c_during_playback_stops_audio_and_motor(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.return_value.get.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            conv.sayit("Hello")
        conv.synthesizer.stop_speaking_async.assert_called_once()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_motor_off_even_if_stopping_audio_fails(self):
        conv = self.conversation()
        conv.synthesizer.speak_ssml_async.return_value.get.return_value = completed()
        conv.synthesizer.stop_speaking_async.return_value.get.side_effect = RuntimeError("stop")
        with self.assertRaisesRegex(RuntimeError, "stop"):
            conv.sayit("Hello")
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_quit_on_first_turn(self):
        for text in ("quit", "Quit.", " QUIT! "):
            with self.subTest(text=text):
                conv = self.conversation()
                self.prompts(conv, [text])
                conv.run()
                conv.client.chat.completions.create.assert_not_called()
                conv.recognizer.stop_continuous_recognition_async.assert_called_once()
                self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_normal_followup_keeps_previous_answer_in_context(self):
        conv = self.conversation()
        self.prompts(conv, ["What are your hands?", "Are they green?", "quit"])
        snapshots = []

        def answer(client, config, messages):
            snapshots.append(list(messages))
            return "Frogs!" if len(snapshots) == 1 else "Yes, green frogs!"

        conv.sayit = Mock()
        with (
            patch.object(speech, "send_message", side_effect=answer),
            patch.object(speech.time, "sleep"),
        ):
            conv.run()
        self.assertEqual(snapshots[1][-2], {"role": "assistant", "content": "Frogs!"})
        self.assertEqual(snapshots[1][-1], {"role": "user", "content": "Are they green?"})
        self.assertEqual(conv.sayit.call_count, 2)

    def test_recognition_error_is_fatal_not_prompt_text(self):
        conv = self.conversation()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = (
            lambda: conv.canceled(SimpleNamespace(reason="Error", error_details="invalid credentials"))
        )
        with self.assertRaisesRegex(RuntimeError, "invalid credentials"):
            conv.run()
        conv.client.chat.completions.create.assert_not_called()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_unexpected_session_stop_is_reported(self):
        conv = self.conversation()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = (
            lambda: conv.session_stopped(SimpleNamespace())
        )
        with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
            conv.run()

    def test_llm_error_stops_recognition_and_motor(self):
        conv = self.conversation()
        self.prompts(conv, ["Hello"])
        with patch.object(speech, "send_message", side_effect=RuntimeError("bad deployment")):
            with self.assertRaisesRegex(RuntimeError, "bad deployment"):
                conv.run()
        conv.recognizer.stop_continuous_recognition_async.assert_called_once()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_motor_off_even_if_stopping_recognition_fails(self):
        conv = self.conversation()
        self.prompts(conv, ["quit"])
        conv.recognizer.stop_continuous_recognition_async.return_value.get.side_effect = (
            RuntimeError("recognizer stop")
        )
        with self.assertRaisesRegex(RuntimeError, "recognizer stop"):
            conv.run()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_history_is_bounded_across_completed_turns(self):
        conv = self.conversation()
        self.prompts(conv, [f"Question {index}" for index in range(30)] + ["quit"])
        conv.sayit = Mock()
        with (
            patch.object(speech, "send_message", return_value="Frog hands!"),
            patch.object(speech.time, "sleep"),
        ):
            conv.run()
        self.assertEqual(conv.messages[0]["role"], "system")
        self.assertEqual(len(conv.messages), 21)
        self.assertEqual(conv.messages[1]["role"], "user")
        self.assertEqual(conv.messages[-1], {"role": "assistant", "content": "Frog hands!"})
        self.assertEqual(conv.sayit.call_count, 30)

    def test_ctrl_c_while_listening_stops_capture_and_motor(self):
        conv = self.conversation()
        conv.heard.get = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            conv.run()
        self.assertFalse(conv.listening)
        conv.recognizer.stop_continuous_recognition_async.assert_called_once()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_recognition_start_failure_stops_capture_and_motor(self):
        conv = self.conversation()
        conv.recognizer.start_continuous_recognition_async.return_value.get.side_effect = (
            RuntimeError("microphone unavailable")
        )
        with self.assertRaisesRegex(RuntimeError, "microphone unavailable"):
            conv.run()
        self.assertFalse(conv.listening)
        conv.recognizer.stop_continuous_recognition_async.assert_called_once()
        self.assertEqual(conv.rock_line.set_value.call_args.args, (0,))

    def test_audio_settle_setting_is_validated(self):
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test",
            "AZURE_OPENAI_DEPLOYMENT": "test",
            "AZURE_SPEECH_KEY": "test",
            "AZURE_SPEECH_REGION": "eastus",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(speech.Settings.from_env().audio_settle_seconds, 0.75)
            os.environ["AUDIO_SETTLE_SECONDS"] = "1.2"
            self.assertEqual(speech.Settings.from_env().audio_settle_seconds, 1.2)
            for value in ("-1", "nan", "inf"):
                os.environ["AUDIO_SETTLE_SECONDS"] = value
                with self.assertRaisesRegex(ValueError, "AUDIO_SETTLE_SECONDS"):
                    speech.Settings.from_env()

    def test_main_releases_gpio_when_conversation_fails(self):
        chip = Mock()
        line = chip.get_line.return_value
        gpiod = SimpleNamespace(Chip=Mock(return_value=chip), LINE_REQ_DIR_OUT=3)
        with (
            patch.object(speech, "load_dotenv") as load_config,
            patch.object(speech.Settings, "from_env", return_value=settings()),
            patch.object(speech.speechsdk, "SpeechConfig"),
            patch.object(speech.speechsdk.audio, "AudioConfig"),
            patch.object(speech.speechsdk.audio, "AudioOutputConfig"),
            patch.object(speech.speechsdk, "SpeechRecognizer"),
            patch.object(speech.speechsdk, "SpeechSynthesizer"),
            patch.object(speech, "AzureOpenAI"),
            patch.object(speech.Conversation, "run", side_effect=RuntimeError("network")),
            patch.dict(sys.modules, {"gpiod": gpiod}),
        ):
            with self.assertRaisesRegex(RuntimeError, "network"):
                speech.main()
        line.request.assert_called_once_with(
            consumer="chuckles-rock", type=3, default_vals=[0]
        )
        line.set_value.assert_called_with(0)
        line.release.assert_called_once()
        chip.close.assert_called_once()
        load_config.assert_called_once_with(
            SCRIPT.with_name(".env"), override=False, interpolate=False
        )

    def test_main_loads_adjacent_config_and_preserves_environment_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / SCRIPT.name
            script.with_name(".env").write_text(
                "AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com\n"
                "AZURE_OPENAI_API_KEY=test-${NOT_EXPANDED}-key\n"
                "AZURE_OPENAI_DEPLOYMENT=from-file\n"
                "AZURE_SPEECH_KEY=test-speech-key\n"
                "AZURE_SPEECH_REGION=eastus\n"
                "GPIO_ENABLED=false\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT": "from-process"}, clear=True),
                patch.object(speech, "__file__", str(script)),
                patch.object(speech.speechsdk, "SpeechConfig"),
                patch.object(speech.speechsdk.audio, "AudioConfig"),
                patch.object(speech.speechsdk.audio, "AudioOutputConfig"),
                patch.object(speech.speechsdk, "SpeechRecognizer"),
                patch.object(speech.speechsdk, "SpeechSynthesizer"),
                patch.object(speech, "AzureOpenAI") as client,
                patch.object(speech, "Conversation") as conversation,
            ):
                speech.main()
                config = conversation.call_args.args[1]
                self.assertEqual(config.deployment, "from-process")
                self.assertEqual(config.speech_region, "eastus")
                self.assertFalse(config.gpio_enabled)
                self.assertEqual(client.call_args.kwargs["api_key"], "test-${NOT_EXPANDED}-key")
                self.assertEqual(client.call_args.kwargs["azure_endpoint"],
                                 "https://example.openai.azure.com")
                conversation.return_value.run.assert_called_once()

    def test_main_uses_refreshable_entra_credentials_for_both_services(self):
        config = speech.Settings(**{
            **settings().__dict__, "auth_mode": "entra", "gpio_enabled": False,
            "tenant_id": "test-tenant", "client_id": "test-app",
            "client_secret": "secret-identity",
            "speech_endpoint": "https://example.cognitiveservices.azure.com/",
        })
        with (
            patch.object(speech, "load_dotenv"),
            patch.object(speech.Settings, "from_env", return_value=config),
            patch.object(speech, "ClientSecretCredential") as identity,
            patch.object(speech, "get_bearer_token_provider") as token_provider,
            patch.object(speech.speechsdk, "SpeechConfig") as speech_config,
            patch.object(speech.speechsdk.audio, "AudioConfig"),
            patch.object(speech.speechsdk.audio, "AudioOutputConfig"),
            patch.object(speech.speechsdk, "SpeechRecognizer"),
            patch.object(speech.speechsdk, "SpeechSynthesizer"),
            patch.object(speech, "AzureOpenAI") as client,
            patch.object(speech.Conversation, "run", side_effect=RuntimeError("network")),
        ):
            with self.assertRaisesRegex(RuntimeError, "network"):
                speech.main()
        credential = identity.return_value.__enter__.return_value
        identity.assert_called_once_with(
            tenant_id="test-tenant", client_id="test-app", client_secret="secret-identity"
        )
        token_provider.assert_called_once_with(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        speech_config.assert_called_once_with(
            token_credential=credential, endpoint=config.speech_endpoint
        )
        self.assertIsNone(client.call_args.kwargs["api_key"])
        self.assertIs(client.call_args.kwargs["azure_ad_token_provider"],
                      token_provider.return_value)
        identity.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
