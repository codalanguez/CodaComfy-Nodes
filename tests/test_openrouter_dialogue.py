import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter.dialogue import script_turns


class ScriptTests(unittest.TestCase):
    cast = "Mara | Kore | Dublin accent\nBen | Puck | Yorkshire accent"
    voices = ["Kore", "Puck"]

    def test_directions_are_separate_and_voices_stay_with_the_speaker(self):
        turns = script_turns(self.cast, "MARA [whisper]: Listen.\nBen: To what?\nMara: That: the bell.",
                             "At the shore", self.voices)
        self.assertEqual([t["text"] for t in turns], ["Listen.", "To what?", "That: the bell."])
        self.assertEqual([t["voice"] for t in turns], ["Kore", "Puck", "Kore"])
        self.assertEqual(turns[0]["instructions"], "At the shore\nDublin accent\nwhisper")
        self.assertEqual(turns[1]["instructions"], "At the shore\nYorkshire accent")
        self.assertNotIn("whisper", turns[2]["instructions"])

    def test_bad_cast_and_script_report_the_line(self):
        cases = [(self.cast, "Mara: Yes.\nBob: Where?", "Script line 2"),
                 (self.cast, "Mara [softly: Hi.", "Script line 1"),
                 (self.cast, "Ben:   ", "Script line 1"),
                 (self.cast + "\nmara | Puck", "Mara: Hi.", "Cast line 3"),
                 ("Ben | typo", "Ben: Hi.", "Cast line 1")]
        for cast, script, error in cases:
            with self.subTest(script=script, cast=cast), self.assertRaisesRegex(ValueError, error):
                script_turns(cast, script, "", self.voices)
