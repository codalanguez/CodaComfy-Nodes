import io
import base64
import json
import os
import socket
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter import client
from openrouter import listening
from openrouter.nodes import OpenRouterPrompt
from openrouter.imaging import image_data_urls
import torch
from PIL import Image


class OpenRouterTests(unittest.TestCase):
    def test_remaining_credit_uses_account_totals_including_zero(self):
        with patch.object(client, "request_json", return_value={"data": {"total_credits": 12, "total_usage": 12}}) as fetch:
            self.assertEqual(client.remaining_credit(), {"scope": "account", "remaining_usd": 0})
            fetch.assert_called_once_with("/credits", timeout=12)

    def test_restricted_key_reports_only_its_own_allowance(self):
        denied = client.OpenRouterError("Account credit unavailable")
        with patch.object(client, "request_json", side_effect=[denied, {"data": {"limit_remaining": 2.5}}]):
            self.assertEqual(client.remaining_credit(), {"scope": "key", "remaining_usd": 2.5})
        with patch.object(client, "request_json", side_effect=[denied, {"data": {"limit_remaining": None}}]):
            with self.assertRaisesRegex(client.OpenRouterError, "unavailable"):
                client.remaining_credit()

    def setUp(self):
        client._catalog = []
        client._catalog_time = 0

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key_never_sends_request(self):
        with patch.object(client, "urlopen") as send:
            with self.assertRaisesRegex(client.OpenRouterError, "OPENROUTER_API_KEY"):
                client.generate("Hello")
            send.assert_not_called()

    def test_optional_model_and_system_message(self):
        response = {"choices": [{"message": {"content": "Answer"}, "finish_reason": "stop"}], "model": "provider/default", "usage": {"total_tokens": 20}}
        with patch.object(client, "request_json", return_value=response) as send:
            text, model, metadata = client.generate("Hello", "Act as an editor")
        payload = send.call_args.args[1]
        self.assertNotIn("model", payload)
        self.assertEqual(payload["messages"], [{"role": "system", "content": "Act as an editor"}, {"role": "user", "content": "Hello"}])
        self.assertEqual((text, model), ("Answer", "provider/default"))
        self.assertEqual(json.loads(metadata)["usage"]["total_tokens"], 20)

    def test_custom_model_overrides_dropdown_and_parts_are_text(self):
        response = {"choices": [{"message": {"content": [{"type": "text", "text": "One"}, {"type": "text", "text": "Two"}]}}]}
        with patch.object(client, "request_json", return_value=response) as send:
            output = client.generate("Hello", model="old/model", custom_model=" new/custom ")
        self.assertEqual(send.call_args.args[1]["model"], "new/custom")
        self.assertEqual(len(send.call_args.args[1]["messages"]), 1)
        self.assertEqual(output[0], "One\nTwo")

    def test_invalid_responses_and_empty_prompt(self):
        for response in [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}]:
            with patch.object(client, "request_json", return_value=response):
                with self.assertRaises(client.OpenRouterError):
                    client.generate("Hello")
        with patch.object(client, "request_json") as send:
            with self.assertRaises(client.OpenRouterError):
                client.generate("  ")
            send.assert_not_called()

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret"})
    def test_authorization_and_safe_errors(self):
        with patch.object(client, "urlopen", return_value=io.BytesIO(b'{"ok": true}')) as send:
            client.request_json("/chat/completions", {"messages": []})
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(request.full_url, client.BASE_URL + "/chat/completions")
        for error in [HTTPError("url", 401, "test-secret", {}, io.BytesIO(b'test-secret')), socket.timeout("test-secret")]:
            with patch.object(client, "urlopen", side_effect=error):
                with self.assertRaises(client.OpenRouterError) as caught:
                    client.request_json("/chat/completions", {})
                self.assertNotIn("test-secret", str(caught.exception))

    def test_video_bad_request_explains_provider_reason_without_echoing_input(self):
        for raw, expected in [
            ({"error": {"code": "InputImageSensitiveContentDetected.PrivacyInformation"}}, "possible real person"),
            ({"message": "Image too large: private-input"}, "size limit"),
            ({"message": "private-input"}, "without a recognized reason"),
        ]:
            body = {"error": {"message": "Provider returned error", "metadata": {"raw": json.dumps(raw)}}}
            err = HTTPError("url", 400, "private-input", {}, io.BytesIO(json.dumps(body).encode()))
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "private-input"}), patch.object(client, "urlopen", side_effect=err):
                with self.assertRaisesRegex(client.OpenRouterError, expected) as caught:
                    client.request_json("/videos", {})
                self.assertNotIn("private-input", str(caught.exception))

    def test_catalog_filter_sort_cache_and_refresh(self):
        data = {"data": [{"id": "z/text"}, {"id": "a/text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}, {"id": "image/only", "architecture": {"output_modalities": ["image"]}}, {"id": "embed/only", "architecture": {"output_modalities": ["embeddings"]}}, {"id": "z/text"}, {}]}
        with patch.object(client, "request_json", return_value=data) as send:
            self.assertEqual([item["id"] for item in client.list_models()], ["a/text", "z/text"])
            client.list_models()
            self.assertEqual(send.call_count, 1)
            client.list_models(refresh=True)
            self.assertEqual(send.call_count, 2)
        self.assertEqual(client.model_choices(), [client.DEFAULT_MODEL, "a/text", "z/text"])

    def test_node_accepts_saved_model_and_preview(self):
        self.assertTrue(OpenRouterPrompt.VALIDATE_INPUTS(model="not-in-current/catalog"))
        self.assertIsInstance(OpenRouterPrompt.VALIDATE_INPUTS(model=123), str)
        with patch.object(client, "generate", return_value=("Hello", "test/model", "{}")):
            result = OpenRouterPrompt().run("Hi")
        self.assertEqual(result["ui"]["text"], ["Hello"])
        self.assertEqual(result["result"][0], "Hello")

    def test_no_credentials_in_node_inputs(self):
        inputs = OpenRouterPrompt.INPUT_TYPES()
        self.assertNotIn("api_key", inputs["required"])
        self.assertNotIn("api_key", inputs["optional"])

    def test_vision_payload_keeps_system_and_sends_images_in_order(self):
        urls = ["data:image/png;base64,first", "data:image/png;base64,second"]
        response = {"choices": [{"message": {"content": "A red image and a blue image."}}]}
        with patch.object(client, "request_json", return_value=response) as send:
            result = client.generate("Compare these", "Be concise", images=urls)
        messages = send.call_args.args[1]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "Be concise"})
        self.assertEqual(messages[1]["content"], [{"type": "text", "text": "Compare these"},
            {"type": "image_url", "image_url": {"url": urls[0]}},
            {"type": "image_url", "image_url": {"url": urls[1]}}])
        self.assertEqual(json.loads(result[2])["image_count"], 2)

    def test_image_without_prompt_gets_description_instruction(self):
        response = {"choices": [{"message": {"content": "Blue"}}]}
        with patch.object(client, "request_json", return_value=response) as send:
            client.generate(" ", images=["data:image/png;base64,test"])
        self.assertEqual(send.call_args.args[1]["messages"][0]["content"][0]["text"], "Describe the attached image.")

    def test_png_batch_preserves_order_pixels_and_source(self):
        images = torch.zeros(2, 3, 4, 3)
        images[0, :, :, 0] = 1
        images[1, :, :, 2] = 1
        original = images.clone()
        urls = image_data_urls(images)
        for url, expected in zip(urls, [(255, 0, 0), (0, 0, 255)]):
            self.assertTrue(url.startswith("data:image/png;base64,"))
            with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as decoded:
                self.assertEqual(decoded.size, (4, 3))
                self.assertEqual(decoded.getpixel((0, 0)), expected)
        self.assertTrue(torch.equal(images, original))

    def test_grayscale_rgba_and_clamped_pixels(self):
        for channels, mode in [(1, "L"), (4, "RGBA")]:
            pixels = torch.full((1, 2, 3, channels), 2.0)
            url = image_data_urls(pixels)[0]
            with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as decoded:
                self.assertEqual(decoded.mode, mode)
                self.assertEqual(decoded.getextrema(), (255, 255) if channels == 1 else ((255, 255),) * 4)

    def test_invalid_images_fail_before_request(self):
        for image in [torch.zeros(0, 2, 2, 3), torch.zeros(2, 2, 3), torch.zeros(1, 2, 2, 2), torch.full((1, 2, 2, 3), float("nan"))]:
            with patch.object(client, "request_json") as send:
                with self.assertRaises(client.OpenRouterError):
                    OpenRouterPrompt().run("Describe", image=image)
                send.assert_not_called()
        self.assertEqual(image_data_urls(None), [])

    def test_node_forwards_optional_image_and_old_workflows_still_work(self):
        self.assertEqual(OpenRouterPrompt.INPUT_TYPES()["optional"]["image"][0], "IMAGE")
        with patch.object(client, "generate", return_value=("Blue", "vision/model", "{}")) as send:
            OpenRouterPrompt().run("Describe", image=torch.zeros(1, 2, 2, 3))
            self.assertEqual(len(send.call_args.kwargs["images"]), 1)
            OpenRouterPrompt().run("Text only")
            self.assertEqual(send.call_args.kwargs["images"], [])

    def test_song_selection_sends_audio_and_exposes_validated_cut(self):
        import av
        audio = {"waveform": torch.zeros(1, 2, 44100 * 3), "sample_rate": 44100}
        answer = json.dumps({"start_seconds": 0.75, "reason": "A rising phrase.", "visual_direction": "Slow push in."})
        with patch.object(client, "request_json", return_value={"choices": [{"message": {"content": answer}}]}) as send:
            result = OpenRouterPrompt().run("Quiet trailer", audio=audio, excerpt_seconds=2)
        payload = send.call_args.args[1]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        recording = payload["messages"][-1]["content"][-1]["input_audio"]
        with av.open(io.BytesIO(base64.b64decode(recording["data"]))) as proxy:
            self.assertAlmostEqual(sum(f.samples for f in proxy.decode(audio=0)) / 24000, 3, places=2)
        self.assertEqual(result["result"][3], 0.75)
        self.assertIn("Quiet trailer", result["result"][4])
        self.assertIn("Slow push in.", result["result"][4])
        self.assertIn("2.750 s", result["ui"]["text"][0])

    def test_bad_selection_cannot_produce_a_short_or_unrelated_cut(self):
        for bad_start in (-1, 3.01, True, "2", float("nan")):
            raw = json.dumps({"start_seconds": bad_start, "reason": "Beat.", "visual_direction": "Pan."})
            with self.subTest(start=bad_start), self.assertRaises(client.OpenRouterError):
                listening.read_selection(raw, latest_start=3)
        audio = {"waveform": torch.zeros(1, 1, 8000), "sample_rate": 8000}
        with patch.object(client, "request_json") as send:
            for prompt, value, seconds in [("Trailer", None, 1), ("Trailer", audio, 2), ("", audio, 1)]:
                with self.assertRaises(client.OpenRouterError):
                    OpenRouterPrompt().run(prompt, audio=value, excerpt_seconds=seconds)
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
