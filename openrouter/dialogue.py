"""Generate a directed conversation and attach it to the selected picture."""

import math
import re

import numpy as np
import torch

from . import client
from .media import DEFAULTS, _MediaNode, list_media


CAST = "Mara | Kore | A soft Dublin accent, curious and quick.\nBen | Puck | A Yorkshire accent, calm and dry."
SCRIPT = "Mara [quietly, hopeful]: Will it float?\nBen [a beat, then amused]: Already is."
TURN = re.compile(r"([^:\[\]]+?)(?:\s*\[([^\[\]]+)\])?\s*:\s*(.+)")


def script_turns(cast, script, scene, voices):
    characters = {}
    for number, line in enumerate(cast.splitlines(), 1):
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split("|", 2)]
        if len(fields) < 2 or not all(fields[:2]):
            raise ValueError(f"Cast line {number}: use Name | Voice | Accent and delivery.")
        name, voice = fields[:2]
        if name.casefold() in characters:
            raise ValueError(f"Cast line {number}: {name} was defined twice.")
        if voice not in voices:
            raise ValueError(f"Cast line {number}: unknown voice {voice!r}. Available voices: {', '.join(voices)}.")
        characters[name.casefold()] = (voice, fields[2] if len(fields) == 3 else "")

    turns = []
    for number, line in enumerate(script.splitlines(), 1):
        if not line.strip():
            continue
        match = TURN.fullmatch(line.strip())
        if not match:
            raise ValueError(f"Script line {number}: use Name [optional delivery cue]: spoken words.")
        name, cue, words = match.groups()
        character = characters.get(name.strip().casefold())
        if character is None:
            raise ValueError(f"Script line {number}: add {name.strip()!r} to the cast.")
        voice, direction = character
        instructions = "\n".join(value for value in (scene.strip(), direction, cue) if value)
        turn = {"text": words, "voice": voice}
        if instructions:
            turn["instructions"] = instructions
        turns.append(turn)
    if not turns:
        raise ValueError("Enter at least one spoken line in the script.")
    return turns


class OpenRouterDialogue(_MediaNode):
    RETURN_TYPES = ("VIDEO", "AUDIO")
    RETURN_NAMES = ("video_with_dialogue", "dialogue")
    DESCRIPTION = "Give each character a voice and accent, then direct each line with an optional bracketed cue. The picture repeats if needed to play the entire scene."

    @classmethod
    def INPUT_TYPES(cls):
        def field(default, tip):
            return ("STRING", {"default": default, "multiline": True, "dynamicPrompts": False, "tooltip": tip})

        return {"required": {
            "model": ([DEFAULTS["dialogue"]],),
            "cast": field(CAST, "One character per line: Name | Gemini voice | accent and usual delivery."),
            "script": field(SCRIPT, "One turn per line: Character [optional emotion, pacing or cue]: Spoken words."),
            "scene_direction": field("Two people watching a paper boat from the bank. A close, natural conversation.",
                                     "Direction shared by all speakers. Leave blank for none."),
            "take": ("INT", {"default": 1, "min": 1, "max": 999999, "tooltip": "Change to request another performance."}),
        }, "optional": {"video": ("VIDEO",)}}

    def run(self, model, cast, script, scene_direction, take, video=None):
        from comfy_api.latest import InputImpl

        if video is not None:
            duration = video.get_duration()
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("The selected video has no playable duration.")
        entry = next((row for row in list_media("dialogue") if row["id"] == model), None)
        if not entry:
            raise ValueError("Select an available Gemini dialogue model.")
        turns = script_turns(cast, script, scene_direction, entry["supported_voices"])
        payload = {"model": model, "input": turns, "response_format": "pcm"}
        if len(turns) == 1:
            turn = turns[0]
            payload.update(input=turn["text"], voice=turn["voice"])
            if "instructions" in turn:
                payload["instructions"] = turn["instructions"]
        with client.request_stream("/audio/speech", payload, timeout=300, accept="audio/pcm") as response:
            if response.headers.get_content_type() != "audio/pcm":
                raise client.OpenRouterError("The speech provider did not return PCM audio.")
            pcm = response.read()
        if not pcm or len(pcm) % 2:
            raise client.OpenRouterError("The speech provider returned empty or incomplete PCM audio.")
        # Gemini TTS emits mono, 24 kHz signed little-endian 16-bit samples.
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        audio = {"sample_rate": 24000, "waveform": torch.from_numpy(samples).reshape(1, 1, -1)}
        result = None
        if video is not None:
            copies = max(1, math.ceil(len(samples) / (24000 * duration)))
            result = InputImpl.VideoFromList([video] * copies, complete_audio=audio)
        return result, audio
