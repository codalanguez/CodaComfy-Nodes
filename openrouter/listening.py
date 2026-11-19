"""Music supervision for an optional recording attached to an OpenRouter prompt."""

import base64
import io
import json
import math

from .client import OpenRouterError


def recording_duration(audio):
    wave, rate = audio["waveform"], audio["sample_rate"]
    if wave.ndim != 3 or wave.shape[0] != 1 or min(wave.shape[1:]) == 0 or rate <= 0:
        raise OpenRouterError("Load one nonempty recording to choose an excerpt.")
    return wave.shape[-1] / rate


def audio_input(audio):
    import av

    # Only send a listening copy. The trim and export branches use the source waveform.
    wave, rate = audio["waveform"][0], audio["sample_rate"]
    encoded = io.BytesIO()
    with av.open(encoded, "w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=24000)
        stream.layout = "mono"
        stream.bit_rate = 48000
        for chunk in wave.split(65536, dim=-1):
            mono = chunk.detach().float().mean(dim=0, keepdim=True).cpu().numpy()
            frame = av.AudioFrame.from_ndarray(mono, format="fltp", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return {"type": "input_audio", "input_audio": {
        "format": "mp3", "data": base64.b64encode(encoded.getvalue()).decode("ascii")}}


def selection_instructions(concept, duration, seconds):
    if not concept.strip():
        raise OpenRouterError("Enter a visual concept so the passage can be matched to it.")
    if seconds <= 0 or seconds > duration:
        raise OpenRouterError(f"The edit needs {seconds} seconds. The song is {duration:.2f} seconds long. "
                              "Choose a longer song, remove a frame, or shorten the shots.")
    return (f"Listen to the entire {duration:.3f}-second recording. Select one continuous {seconds}-second "
            f"passage that best serves the user's visual concept. Start between 0 and {duration - seconds:.6f} "
            "seconds from the beginning. Consider phrasing, lyrics if present, instrumentation, changes in energy, "
            "and a satisfying ending. Prefer natural musical boundaries. Return a JSON object with start_seconds "
            "(a number), reason (name specific audible features that support the concept), and visual_direction "
            "(concise camera and motion direction for animating an individual reference frame to this passage).")


def read_selection(raw, latest_start):
    try:
        data = json.loads(raw)
        start, reason, direction = data["start_seconds"], data["reason"], data["visual_direction"]
        if type(start) not in (int, float) or not math.isfinite(start) or not 0 <= start <= latest_start + 1e-6:
            raise ValueError
        if not all(isinstance(v, str) and v.strip() for v in (reason, direction)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise OpenRouterError("The listening model returned an invalid passage. Retry or choose another audio-input model.") from None
    return min(start, latest_start), reason.strip(), direction.strip()
