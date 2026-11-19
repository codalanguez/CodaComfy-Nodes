"""Small OpenRouter client; no SDK or credentials in workflow JSON."""

import json
import math
import os
import socket
import threading
import time
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "(OpenRouter default)"
_catalog = []
_catalog_time = 0.0
_catalog_lock = threading.Lock()


class OpenRouterError(RuntimeError):
    pass


def video_rejection_hint(body):
    """Recognize provider validation errors without printing request data or credentials."""
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError, UnicodeError):
        error = None
    if not isinstance(error, dict):
        return "The video request was rejected without a recognized reason."
    metadata = error.get("metadata")
    raw = metadata.get("raw") if isinstance(metadata, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {"message": raw}
    if isinstance(raw, dict):
        raw = raw.get("error", raw)
    def fields(item):
        return " ".join(str(item.get(k, "")) for k in ("code", "message", "param")) if isinstance(item, dict) else ""
    detail = (fields(raw) + " " + fields(error)).casefold()
    for markers, hint in (
        (("privacyinformation", "real person", "real human", "photorealistic human"), "The provider flagged a possible real person in the reference. Use an eligible reference or its authorized portrait workflow."),
        (("sensitivecontent", "content policy", "safety filter", "content moderation"), "The provider declined the input under its content policy."),
        (("payload too large", "request entity too large", "image too large", "image size exceeds", "maximum request size"), "The provider reported that the image or request exceeds its size limit."),
        (("invalid aspect ratio", "unsupported aspect ratio", "aspect_ratio"), "The provider rejected the aspect ratio for this shot."),
        (("unsupported duration", "supported durations", "invalid duration", "duration must"), "The provider rejected the requested duration for this shot."),
        (("invalid image", "unsupported image", "failed to decode image"), "The provider could not accept the reference image format or data."),
    ):
        if any(marker in detail for marker in markers):
            return hint
    return "The video request was rejected without a recognized reason."


@contextmanager
def request_stream(path, payload=None, timeout=120, require_key=True, accept="application/json"):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if require_key and not key:
        raise OpenRouterError("Set OPENROUTER_API_KEY in the environment of the ComfyUI server, then restart ComfyUI.")
    headers = {"Accept": accept, "User-Agent": "CodaComfyTools/0.1", "X-Title": "CodaComfyTools"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(BASE_URL + path, data=body, headers=headers)
    if key:
        request.add_unredirected_header("Authorization", "Bearer " + key)
    try:
        with urlopen(request, timeout=timeout) as response:
            yield response
    except HTTPError as exc:
        # Do not surface arbitrary upstream bodies, which may echo credentials/prompts.
        hints = {401: "Check OPENROUTER_API_KEY.", 402: "Check your OpenRouter credits.",
                 403: "The request was denied.", 404: "Check the model ID and its available providers.",
                 429: "Rate limited; wait before running again."}
        status = exc.code
        if status == 400 and path == "/videos":
            try:
                hints[400] = video_rejection_hint(exc.read(32768))
            except (OSError, ValueError):
                hints[400] = "The video provider rejected this shot."
        exc.close()
        raise OpenRouterError("OpenRouter HTTP {}. {}".format(status, hints.get(status, "Request failed; check OpenRouter status or model availability."))) from None
    except (URLError, socket.timeout, TimeoutError, OSError):
        raise OpenRouterError("OpenRouter connection failed or timed out. Check connectivity and timeout_seconds.") from None


def request_json(path, payload=None, timeout=120, require_key=True):
    try:
        with request_stream(path, payload, timeout, require_key) as response:
            result = json.load(response)
    except (ValueError, UnicodeError):
        raise OpenRouterError("OpenRouter returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise OpenRouterError("OpenRouter returned an unexpected response.")
    if result.get("error"):
        raise OpenRouterError("OpenRouter reported an error. Check model availability, credits, and request settings.")
    return result


def remaining_credit():
    """Return only the amount and its scope, never the account/key metadata."""
    try:
        data = request_json("/credits", timeout=12).get("data") or {}
        total, used = data.get("total_credits"), data.get("total_usage")
        if all(type(value) in (int, float) and math.isfinite(value) for value in (total, used)):
            return {"scope": "account", "remaining_usd": total - used}
    except OpenRouterError:
        pass  # Some generation keys can only read their own allowance.
    data = request_json("/key", timeout=12).get("data") or {}
    remaining = data.get("limit_remaining")
    if type(remaining) in (int, float) and math.isfinite(remaining):
        return {"scope": "key", "remaining_usd": remaining}
    raise OpenRouterError("Account balance unavailable. This key has no reported remaining allowance.")


def list_models(refresh=False):
    global _catalog, _catalog_time
    with _catalog_lock:
        if _catalog and not refresh and time.monotonic() - _catalog_time < 300:
            return list(_catalog)
        result = request_json("/models", timeout=30, require_key=False)
        data = result.get("data")
        if not isinstance(data, list):
            raise OpenRouterError("OpenRouter model catalog is missing its data list.")
        models = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                continue
            architecture = item.get("architecture") or {}
            if not isinstance(architecture, dict):
                continue
            inputs = architecture.get("input_modalities")
            outputs = architecture.get("output_modalities")
            if (inputs is not None and "text" not in inputs) or (outputs is not None and "text" not in outputs):
                continue
            models.append({"id": item["id"], "name": item.get("name", item["id"]),
                           "input_modalities": inputs or [],
                           "context_length": item.get("context_length"), "pricing": item.get("pricing", {})})
        if not models:
            raise OpenRouterError("OpenRouter returned no text models. You can still use a custom model ID.")
        _catalog = sorted({item["id"]: item for item in models}.values(), key=lambda item: item["id"])
        _catalog_time = time.monotonic()
        return list(_catalog)


def model_choices():
    # Node metadata must load instantly, even without internet access.
    return [DEFAULT_MODEL] + [item["id"] for item in _catalog]


def generate(prompt, system_prompt="", model=DEFAULT_MODEL, custom_model="", max_tokens=2048, timeout_seconds=120,
             images=None, audio_part=None, json_mode=False):
    images = images or []
    if not prompt.strip():
        if audio_part:
            prompt = "Describe the attached recording."
        elif images:
            prompt = "Describe the attached image." if len(images) == 1 else "Describe the attached images in order."
        else:
            raise OpenRouterError("Enter a prompt or connect an image or recording.")
    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    content = prompt
    if images or audio_part:
        content = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        if audio_part:
            content.append(audio_part)
    messages.append({"role": "user", "content": content})
    payload = {"messages": messages, "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    selected = custom_model.strip() or (model or "").strip()
    if selected and selected != DEFAULT_MODEL:
        payload["model"] = selected
    result = request_json("/chat/completions", payload, timeout=timeout_seconds)
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise OpenRouterError("OpenRouter returned no completion choices.")
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "\n".join(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError("OpenRouter returned no text. Try another model or increase max_tokens for a reasoning model.")
    metadata = {"model": result.get("model", selected if selected != DEFAULT_MODEL else ""),
                "usage": result.get("usage", {}), "finish_reason": choice.get("finish_reason"),
                "image_count": len(images)}
    return content, str(metadata["model"] or ""), json.dumps(metadata, ensure_ascii=False)
