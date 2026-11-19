"""Authenticated media catalogs, video jobs and streamed audio completions."""

import base64
import binascii
import io
import json
import logging
import threading
import time
from urllib.parse import quote

from . import client
from .imaging import image_data_urls


DEFAULTS = {"video": "bytedance/seedance-2.5-flash", "audio": "google/lyria-3-clip-preview",
            "dialogue": "google/gemini-3.8-flash-lite-tts"}
_catalogs = {}
_catalog_lock = threading.Lock()


def list_media(kind, refresh=False):
    if kind == "speaking_video":
        return [row for row in list_media("video", refresh) if row.get("generate_audio") is True]
    endpoint = {"video": "/videos/models", "audio": "/models?output_modalities=audio",
                "dialogue": "/models?output_modalities=speech"}[kind]
    with _catalog_lock:
        timestamp, models = _catalogs.get(kind, (0, []))
        if models and not refresh and time.monotonic() - timestamp < 300:
            return models
        data = client.request_json(endpoint, timeout=30).get("data")
        if not isinstance(data, list):
            raise client.OpenRouterError("Missing OpenRouter media catalog.")
        models = {}
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
                continue
            arch = row.get("architecture") or {}
            if kind == "dialogue" and (row["id"] not in (DEFAULTS["dialogue"], "google/gemini-3.8-flash-tts")
                                       or not row.get("supported_voices")):
                continue
            if kind == "audio" and ("audio" not in arch.get("output_modalities", []) or "text" not in arch.get("input_modalities", ["text"])):
                continue
            models[row["id"]] = row
        if not models:
            raise client.OpenRouterError(f"OpenRouter returned no {kind} generation models.")
        result = [models[key] for key in sorted(models)]
        _catalogs[kind] = time.monotonic(), result
        return result


def wait_for_video(job_id):
    from nodes import before_node_execution

    path = "/videos/" + quote(job_id, safe="")
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        before_node_execution()
        status = client.request_json(path, timeout=30).get("status")
        if status == "completed":
            with client.request_stream(path + "/content?index=0", accept="video/*") as response:
                buffer = io.BytesIO()
                while chunk := response.read(1024 * 1024):
                    before_node_execution()
                    buffer.write(chunk)
                buffer.seek(0)
                return buffer
        if status not in ("pending", "queued", "processing", "in_progress"):
            raise client.OpenRouterError(f"Video job {job_id} ended with status {status!r}. Check the job in OpenRouter.")
        time.sleep(3)
    raise client.OpenRouterError("Timed out waiting for the video.")


def frame_images(model, first_frame, last_frame, entry):
    if first_frame is None:
        if last_frame is not None:
            raise client.OpenRouterError("Connect a first frame before adding a last frame.")
        return []
    supported = entry.get("supported_frame_images")
    frames = []
    for role, image in (("first_frame", first_frame), ("last_frame", last_frame)):
        if image is None:
            continue
        if supported is not None and role not in supported:
            raise client.OpenRouterError(f"{model} does not support {role}. Select another video model or disconnect that image.")
        if getattr(image, "ndim", None) != 4 or image.shape[0] != 1:
            raise client.OpenRouterError(f"Connect one IMAGE to {role}. For a batch, select one image first.")
        url, = image_data_urls(image)
        frames.append({"type": "image_url", "frame_type": role, "image_url": {"url": url}})
    return frames


def generate_video(model, prompt, duration, resume_job_id, first_frame=None, last_frame=None,
                   *, generate_audio=False, resolution=None, aspect_ratio=None):
    job_id = resume_job_id.strip()
    if not job_id:
        if not prompt.strip():
            raise client.OpenRouterError("Enter a video prompt.")
        entry = next((row for row in list_media("video") if row["id"] == model), {})
        durations = sorted(n for n in entry.get("supported_durations") or [] if n > 0)
        if duration and durations and duration not in durations:
            choices = ", ".join(map(str, durations))
            raise client.OpenRouterError(
                f"{model} does not accept {duration} seconds per shot. Supported durations: {choices} seconds. "
                "Change the linked Seconds per shot control, or duration_seconds on the video node."
            )
        payload = {"model": model, "prompt": prompt, "generate_audio": generate_audio}
        if resolution:
            payload["resolution"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        frames = frame_images(model, first_frame, last_frame, entry)
        if frames:
            payload["frame_images"] = frames
        if duration:
            payload["duration"] = duration
        job_id = client.request_json("/videos", payload).get("id")
        if not isinstance(job_id, str) or not job_id:
            raise client.OpenRouterError("OpenRouter did not return a video job ID. Check your OpenRouter activity before queuing again.")
    logging.info("OpenRouter video job %s (use resume_job_id to retrieve it again)", job_id)
    try:
        return wait_for_video(job_id)
    except client.OpenRouterError as exc:
        raise client.OpenRouterError(f"{exc} Job ID: {job_id}. Use resume_job_id to fetch the existing job.") from None


def generate_audio(model, prompt, voice):
    from nodes import before_node_execution

    config = {"format": "wav"}
    if voice.strip() or model.startswith("openai/"):
        config["voice"] = voice.strip() or "alloy"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "modalities": ["text", "audio"], "audio": config, "stream": True}
    chunks = []
    complete = False
    with client.request_stream("/chat/completions", payload, timeout=240, accept="text/event-stream") as response:
        for line in response:
            before_node_execution()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                complete = True
                break
            event = json.loads(data)
            if event.get("error"):
                raise client.OpenRouterError("OpenRouter reported an audio stream error. Check your model and voice.")
            for choice in event.get("choices", []):
                if choice.get("finish_reason") == "error":
                    raise client.OpenRouterError("The audio provider stopped with an error.")
                fragment = ((choice.get("delta") or {}).get("audio") or {}).get("data")
                if fragment:
                    chunks.append(fragment)
    if not complete or not chunks:
        raise client.OpenRouterError("OpenRouter returned an incomplete or empty audio stream.")
    try:
        return io.BytesIO(base64.b64decode("".join(chunks), validate=True))
    except (ValueError, binascii.Error):
        raise client.OpenRouterError("OpenRouter returned invalid encoded audio.") from None


def silent_video(buffer):
    import av
    from comfy_api.latest import InputImpl

    # Some providers always include audio. Copy only the picture packets in that case.
    with av.open(buffer) as source:
        if not source.streams.video:
            raise client.OpenRouterError("The completed job contained no video stream.")
        if source.streams.audio:
            picture = source.streams.video[0]
            silent = io.BytesIO()
            with av.open(silent, mode="w", format="mp4") as output:
                stream = output.add_stream_from_template(picture)
                for packet in source.demux(picture):
                    if packet.dts is not None:
                        packet.stream = stream
                        output.mux(packet)
            buffer = silent
    buffer.seek(0)
    return InputImpl.VideoFromFile(buffer)


class _MediaNode:
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"

    @classmethod
    def VALIDATE_INPUTS(cls, model):
        # The browser populates the live picker after node definitions are loaded.
        return isinstance(model, str) and bool(model.strip()) or "Select an OpenRouter model."


class OpenRouterVideo(_MediaNode):
    RETURN_TYPES = ("VIDEO",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ([DEFAULTS["video"]],),
            "prompt": ("STRING", {"multiline": True, "dynamicPrompts": False}),
            "duration_seconds": ("INT", {"default": 0, "min": 0, "max": 600, "tooltip": "0 uses the model's default duration."}),
            "take": ("INT", {"default": 1, "min": 1, "max": 999999, "tooltip": "Change to request another take."}),
            "resume_job_id": ("STRING", {"default": "", "tooltip": "Retrieve a previously submitted OpenRouter video job."}),
        }, "optional": {
            "first_frame": ("IMAGE", {"tooltip": "Starting image for a new video. Leave open for text-only generation."}),
            "last_frame": ("IMAGE", {"tooltip": "Optional ending image. Connect together with a first frame on a model supporting both."}),
            "use_last_frame": ("BOOLEAN", {"default": True, "tooltip": "Include a connected ending image in the generation request."}),
            "aspect_ratio": (["auto", "9:16", "16:9", "1:1", "4:3", "3:4", "21:9"], {"tooltip": "Use a ratio supported by the selected model. Auto leaves the size to the provider."}),
        }}

    def run(self, model, prompt, duration_seconds, take, resume_job_id, first_frame=None, last_frame=None, use_last_frame=True, aspect_ratio="auto"):
        ending = last_frame if use_last_frame else None
        options = {} if aspect_ratio == "auto" else {"aspect_ratio": aspect_ratio}
        return (silent_video(generate_video(model, prompt, duration_seconds, resume_job_id, first_frame, ending, **options)),)


class OpenRouterSound(_MediaNode):
    RETURN_TYPES = ("VIDEO", "AUDIO")
    RETURN_NAMES = ("video_with_sound", "soundtrack")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ([DEFAULTS["audio"]],),
            "prompt": ("STRING", {"multiline": True, "dynamicPrompts": False}),
            "voice": ("STRING", {"default": "", "tooltip": "Optional voice for speech. OpenAI audio defaults to alloy."}),
            "take": ("INT", {"default": 1, "min": 1, "max": 999999, "tooltip": "Change to generate another soundtrack."}),
        }, "optional": {"video": ("VIDEO",)}}

    def run(self, model, prompt, voice, take, video=None):
        from comfy_api.latest import InputImpl
        from comfy_extras.nodes_audio import load
        from torch.nn.functional import pad

        if not prompt.strip():
            raise client.OpenRouterError("Describe the sound or enter narration in the sound prompt.")
        if video is not None:
            duration = video.get_duration()
            prompt = f"Soundtrack for a {duration:.2f}-second video.\n{prompt}"
        waveform, rate = load(generate_audio(model, prompt, voice))
        if video is not None:
            length = round(duration * rate)
            waveform = waveform[:, :length]
            waveform = pad(waveform, (0, length - waveform.shape[-1]))
        audio = {"waveform": waveform.unsqueeze(0), "sample_rate": rate}
        scored = InputImpl.VideoFromList([video], complete_audio=audio) if video is not None else None
        return scored, audio


class MixDialogueSoundtrack:
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"
    RETURN_TYPES = ("VIDEO", "AUDIO")
    RETURN_NAMES = ("mixed_video", "mixed_audio")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"dialogue_video": ("VIDEO",), "soundtrack": ("AUDIO",),
                             "soundtrack_db": ("INT", {"default": -18, "min": -60, "max": 6,
                                                       "tooltip": "Soundtrack level underneath the original dialogue."})}}

    def run(self, dialogue_video, soundtrack, soundtrack_db):
        from comfy_api.latest import InputImpl
        from comfy_extras.nodes_audio import AudioAdjustVolume, AudioMerge, load

        if isinstance(dialogue_video, (InputImpl.VideoFromFile, InputImpl.VideoFromList)):
            samples, rate = load(dialogue_video.get_stream_source())
            voice = {"waveform": samples.unsqueeze(0), "sample_rate": rate}
        else:
            voice = dialogue_video.get_components().audio
        if voice is None:
            raise ValueError("Connect a video containing a dialogue track.")
        bed = AudioAdjustVolume.execute(soundtrack, soundtrack_db).result[0]
        mixed = AudioMerge.execute(voice, bed, "add").result[0]
        return InputImpl.VideoFromList([dialogue_video], complete_audio=mixed), mixed
