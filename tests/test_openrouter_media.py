import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter import client, media


class MediaTests(unittest.TestCase):
    def test_veo_duration_is_checked_before_frame_encoding_or_submission(self):
        model = {"id": "google/veo-3.1-fast", "supported_durations": [4, 6, 8]}
        frame = torch.zeros(1, 2, 3, 3)
        with patch.object(media, "list_media", return_value=[model]), patch.object(media, "image_data_urls") as encode, \
                patch.object(client, "request_json") as submit:
            with self.assertRaisesRegex(client.OpenRouterError, "4, 6, 8"):
                media.OpenRouterVideo().run(model["id"], "Moonlit lake", 5, 1, "", frame)
            encode.assert_not_called()
            submit.assert_not_called()
        with patch.object(media, "list_media", return_value=[model]), patch.object(media, "wait_for_video"), \
                patch.object(client, "request_json", return_value={"id": "job"}) as submit:
            media.generate_video(model["id"], "Moonlit lake", 6, "", frame)
            self.assertEqual(submit.call_args.args[1]["duration"], 6)

    def test_video_frame_payloads_and_last_frame_toggle(self):
        first = torch.zeros((1, 2, 3, 3))
        last = torch.ones_like(first)
        model = {"id": "v/model", "supported_frame_images": ["first_frame", "last_frame"]}
        cases = [(None, None, True, []), (first, None, True, ["first_frame"]),
                 (first, last, False, ["first_frame"]), (first, last, True, ["first_frame", "last_frame"])]
        for start, end, enabled, roles in cases:
            with self.subTest(roles=roles), patch.object(media, "list_media", return_value=[model]), \
                    patch.object(client, "request_json", return_value={"id": "job"}) as submit, \
                    patch.object(media, "wait_for_video"), patch.object(media, "silent_video"):
                media.OpenRouterVideo().run("v/model", "shot", 5, 1, "", start, end, enabled)
                payload = submit.call_args.args[1]
                frames = payload.get("frame_images", [])
                self.assertEqual("frame_images" in payload, bool(roles))
                self.assertEqual([f["frame_type"] for f in frames], roles)
                for f in frames:
                    self.assertEqual(f["type"], "image_url")
                    self.assertTrue(f["image_url"]["url"].startswith("data:image/png;base64,"))
                if len(frames) == 2:
                    self.assertNotEqual(frames[0]["image_url"], frames[1]["image_url"])

    def test_invalid_and_unsupported_frames_fail_before_submission(self):
        frame = torch.zeros((1, 2, 2, 3))
        model = {"id": "v/model", "supported_frame_images": ["first_frame"]}
        with patch.object(media, "list_media", return_value=[model]), patch.object(client, "request_json") as submit:
            for start, end in [(None, frame), (frame, frame), (frame.repeat(2, 1, 1, 1), None)]:
                with self.assertRaises(client.OpenRouterError):
                    media.generate_video("v/model", "shot", 5, "", start, end)
            submit.assert_not_called()

    def test_catalogs_are_auth_required_and_independent(self):
        video = {"id": "v/model", "supported_durations": [5]}
        music = {"id": "a/model", "architecture": {"output_modalities": ["text", "audio"]}}
        with patch.dict(media._catalogs, clear=True), patch.object(client, "request_json") as get:
            get.side_effect = [{"data": [video]}, {"data": [music, {"id": "text/only"}]}]
            self.assertEqual(media.list_media("video"), [video])
            self.assertEqual(media.list_media("audio"), [music])
            self.assertEqual(media.list_media("video"), [video])
            self.assertEqual([call.args[0] for call in get.call_args_list],
                             ["/videos/models", "/models?output_modalities=audio"])
            self.assertTrue(all(call.kwargs.get("require_key", True) for call in get.call_args_list))

    def test_resume_only_polls_existing_job(self):
        with patch.object(client, "request_json") as submit, patch.object(media, "wait_for_video") as wait:
            media.generate_video("provider/video", "", 0, "existing")
            submit.assert_not_called()
            wait.assert_called_once_with("existing")

    def test_stream_must_finish_before_returning_audio(self):
        chunk = b'data: {"choices":[{"delta":{"audio":{"data":"UklGRg=="}}}]}\n\n'
        with patch.dict(sys.modules, {"nodes": SimpleNamespace(before_node_execution=lambda: None)}):
            with patch.object(client, "request_stream", return_value=io.BytesIO(chunk)):
                with self.assertRaisesRegex(client.OpenRouterError, "incomplete"):
                    media.generate_audio("some/audio", "a cue", "")
            with patch.object(client, "request_stream", return_value=io.BytesIO(chunk + b"data: [DONE]\n")):
                self.assertEqual(media.generate_audio("some/audio", "a cue", "").read(), b"RIFF")
