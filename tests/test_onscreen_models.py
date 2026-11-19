import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter import media, onscreen


class OnScreenModelTests(unittest.TestCase):
    def test_solo_pair_and_ensemble_from_existing_cards(self):
        node = onscreen.OnScreenDialogue()
        card = onscreen.ScreenCharacter()
        def make(name, previous=None):
            return card.run(name, "coat " + name, "accent " + name, "cue " + name, "Line " + name, previous)[0]

        a, b = make("A"), make("B")
        extras = make("D", make("C"))
        api = SimpleNamespace(InputImpl=SimpleNamespace(VideoFromFile=lambda buf: buf))
        with patch.dict(sys.modules, {"comfy_api.latest": api}), patch.object(onscreen, "list_media",
                return_value=[{"id": onscreen.DEFAULT_MODEL}]), patch.object(onscreen, "generate_video") as submit:
            for second, more, expected in [(None, None, "A"), (b, None, "AB"), (b, extras, "ABCD")]:
                node.run(a, speaker_b=second, additional_characters=more, scene=onscreen.FAIREST_CAST)
                prompt = submit.call_args.args[1]
                self.assertEqual([line.split('Spoken line: ')[1] for line in prompt.splitlines()
                                  if 'Spoken line: ' in line], [f'"Line {name}"' for name in expected])
                for name in expected:
                    for prefix in ("coat", "accent", "cue"):
                        self.assertIn(f"{prefix} {name}", prompt)
                self.assertTrue(submit.call_args.kwargs["generate_audio"])
            for old_scene in (onscreen.SCENE, onscreen.FAIREST_PAIR):
                node.run(a, scene=old_scene)
                self.assertIn('"Line A"', submit.call_args.args[1])
                node.run(a, b, old_scene, "6", 1)
                self.assertIn('"Line B"', submit.call_args.args[1])
        self.assertIn("speaker_b", node.INPUT_TYPES()["optional"])
        self.assertIn("speaker_a", node.INPUT_TYPES()["optional"])

    def test_empty_stacks_and_ordered_multi_shot_guides(self):
        import torch
        cards = onscreen.ScreenCharacter()
        frame = onscreen.FrameStack()
        disabled = cards.run("", "", "", "", "", enabled=False)[0]
        self.assertEqual(disabled, [])
        batch = torch.arange(3).reshape(3, 1, 1, 1)
        guides = frame.run(True, frame=batch[:2])[0]
        guides = frame.run(False, guides, batch)[0]
        guides = frame.run(True, guides, batch[2:])[0]
        self.assertEqual([g.item() for g in guides], [0, 1, 2])
        cast = []
        for name in ["A", "B", "C"]:
            cast = cards.run(name, name+" coat", "", "waves", name+" speaks", cast)[0]
        api = SimpleNamespace(InputImpl=SimpleNamespace(VideoFromFile=lambda x: x, VideoFromList=tuple))
        node = onscreen.OnScreenDialogue()
        with patch.dict(sys.modules, {"comfy_api.latest": api}), patch.object(onscreen, "list_media",
                return_value=[{"id": onscreen.DEFAULT_MODEL}]), patch.object(onscreen, "generate_video") as submit:
            node.run(scene=onscreen.FAIREST_CAST)
            self.assertIn("No characters, speech or narration", submit.call_args.args[1])
            self.assertEqual(submit.call_args.args[4:6], (None, None))
            for paired, endpoints in [(True, [(0, 1), (1, 2)]), (False, [(0, None), (1, None), (2, None)])]:
                submit.reset_mock()
                result, = node.run(cast, frame_stack=guides, scene=onscreen.FAIREST_CAST, use_last_frame=paired)
                self.assertEqual(len(result), len(endpoints))
                actual = [(c.args[4].item(), None if c.args[5] is None else c.args[5].item()) for c in submit.call_args_list]
                self.assertEqual(actual, endpoints)
                prompts = " ".join(c.args[1] for c in submit.call_args_list)
                for name in "ABC":
                    self.assertEqual(prompts.count(f'"{name} speaks"'), 1)
            submit.reset_mock()
            node.run(cast[:1], frame_stack=guides, scene=onscreen.FAIREST_CAST)
            self.assertIn("Silent reaction", submit.call_args.args[1])
            self.assertNotIn("unpopulated", submit.call_args.args[1])

    def test_picker_uses_audio_capability_and_refreshes_the_video_catalog(self):
        rows = [{"id": "v/voice", "generate_audio": True}, {"id": "v/mute", "generate_audio": False},
                {"id": "v/unknown"}]
        with patch.dict(media._catalogs, clear=True), patch.object(media.client, "request_json") as fetch:
            fetch.return_value = {"data": rows}
            self.assertEqual(media.list_media("speaking_video"), [rows[0]])
            self.assertEqual(len(media.list_media("video")), 3)
            self.assertEqual(fetch.call_count, 1)
            media.list_media("speaking_video", True)
            self.assertEqual(fetch.call_count, 2)

    def test_model_choice_and_paired_frame_duration_reach_submission(self):
        models = [dict(id=model, supported_durations=[5, 6, 8], supported_resolutions=["720p"],
                       supported_aspect_ratios=["16:9"]) for model in (onscreen.DEFAULT_MODEL, "other/native")]
        speaker = onscreen.ScreenCharacter().run("A", "in blue", "Welsh", "asks", "Ready?")[0]
        api = SimpleNamespace(InputImpl=SimpleNamespace(VideoFromFile=lambda buffer: buffer))
        node = onscreen.OnScreenDialogue()
        with patch.dict(sys.modules, {"comfy_api.latest": api}), patch.object(onscreen, "list_media", return_value=models), \
                patch.object(onscreen, "generate_video", return_value=io.BytesIO()) as submit:
            for model, expected_seconds in [(onscreen.DEFAULT_MODEL, 8), ("other/native", 5)]:
                node.run(speaker, speaker, onscreen.SCENE, "5", 1, "opening", "ending", True, model)
                self.assertEqual(submit.call_args.args[0], model)
                self.assertEqual(submit.call_args.args[2:6], (expected_seconds, "", "opening", "ending"))
                self.assertTrue(submit.call_args.kwargs["generate_audio"])
            submit.reset_mock()
            with self.assertRaisesRegex(ValueError, "durations"):
                node.run(speaker, speaker, onscreen.SCENE, "11", 1, model="other/native")
            submit.assert_not_called()
