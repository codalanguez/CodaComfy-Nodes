"""Read explicit trigger fields, never infer triggers from training tag counts."""
import json
from pathlib import Path

FIELDS = ("trainedWords", "trigger_words", "triggerWords", "activation text",
          "activation_text", "ss_trigger_words", "trigger_phrase")
MAX_METADATA_BYTES = 8 * 1024 * 1024


def extract_triggers(data):
    if not isinstance(data, dict):
        return []
    result = []
    for field in FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, list):
                    value = decoded
            except (ValueError, TypeError):
                pass
            if isinstance(value, str):
                value = [value]
        if isinstance(value, list):
            result.extend(word.strip() for word in value if isinstance(word, str) and word.strip())
    for key in ("civitai", "modelVersion", "metadata", "__metadata__"):
        result.extend(extract_triggers(data.get(key)))
    return list(dict.fromkeys(result))


def read_triggers(model):
    model = Path(model).resolve()
    result = []
    for base in (model.with_suffix(""), model):
        for suffix in (".metadata.json", ".civitai.info", ".info", ".json"):
            path = Path(str(base) + suffix)
            try:
                if path.resolve().parent != model.parent or path.stat().st_size > MAX_METADATA_BYTES:
                    continue
                with path.open(encoding="utf-8-sig") as stream:
                    result.extend(extract_triggers(json.load(stream)))
            except (OSError, ValueError, UnicodeError, RecursionError):
                continue
    # Only read the bounded JSON header, never the tensor data.
    if not result and model.suffix.lower() == ".safetensors":
        try:
            with model.open("rb") as stream:
                length = int.from_bytes(stream.read(8), "little")
                if 0 < length <= MAX_METADATA_BYTES:
                    header = json.loads(stream.read(length))
                    result.extend(extract_triggers(header.get("__metadata__")))
        except (OSError, ValueError, UnicodeError, AttributeError, RecursionError):
            pass
    return list(dict.fromkeys(result))
