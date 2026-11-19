"""Build the same (filename, model strength, CLIP strength) tuples as Easy Use."""
import json
import math
import folder_paths
from .metadata import read_triggers


class CodaLoraStackPreview:
    CATEGORY = "Coda/LoRA"
    DESCRIPTION = "Build an Easy Use / Comfyroll LoRA stack with local image previews and trigger words."
    SEARCH_ALIASES = ["lora", "stack", "preview", "triggers", "lora browser"]
    RETURN_TYPES = ("LORA_STACK", "STRING")
    RETURN_NAMES = ("lora_stack", "triggers")
    FUNCTION = "stack"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "toggle": ("BOOLEAN", {"default": True, "label_on": "Enabled", "label_off": "Disabled"}),
            "mode": (["simple", "advanced"],),
            "loras": ("STRING", {"default": "[]"}),
        }, "optional": {"optional_lora_stack": ("LORA_STACK",)}}

    def stack(self, toggle, mode, loras, optional_lora_stack=None):
        if not toggle:
            return (None, "")
        if mode not in ("simple", "advanced"):
            raise ValueError("LoRA stack mode must be simple or advanced.")
        try:
            entries = json.loads(loras)
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid saved LoRA stack. Reopen the node and select your LoRAs.") from error
        if not isinstance(entries, list) or len(entries) > 10:
            raise ValueError("The stack must contain a list of up to 10 LoRA slots.")
        result = [tuple(item) for item in (optional_lora_stack or []) if item[0] != "None"]
        available = set(folder_paths.get_filename_list("loras"))
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Each LoRA slot must be an object.")
            name = entry.get("name")
            if not entry.get("enabled", True) or not name or name == "None":
                continue
            if name not in available:
                raise ValueError(f"LoRA not found: {name}")
            model = float(entry.get("strength", 1.0))
            clip = model if mode == "simple" else float(entry.get("clip_strength", 1.0))
            if not all(math.isfinite(value) and -10 <= value <= 10 for value in (model, clip)):
                raise ValueError("LoRA strengths must be finite numbers between -10 and 10.")
            result.append((name, model, clip))
        triggers = []
        for name, model, clip in result:
            if model == 0 and clip == 0:
                continue
            path = folder_paths.get_full_path("loras", name)
            if path:
                triggers.extend(read_triggers(path))
        return (result, ", ".join(dict.fromkeys(triggers)))
