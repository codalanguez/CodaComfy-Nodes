"""ComfyUI nodes for OpenRouter completion and catalog listing."""

import json
from . import client
from .dialogue import OpenRouterDialogue
from .onscreen import FrameStack, OnScreenDialogue, ScreenCharacter
from .imaging import image_data_urls
from . import listening
from .media import MixDialogueSoundtrack, OpenRouterVideo, OpenRouterSound, list_media


class OpenRouterPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "system_prompt": ("STRING", {"multiline": True, "default": "You are a helpful assistant.", "dynamicPrompts": False,
                    "tooltip": "System Prompt: define the assistant's role, rules, style, and output format. Sent as a separate system message."}),
                "prompt": ("STRING", {"multiline": True, "default": "", "dynamicPrompts": False}),
                "max_tokens": ("INT", {"default": 2048, "min": 1, "max": 1000000}),
                "timeout_seconds": ("INT", {"default": 120, "min": 5, "max": 1800}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True}),
            },
            "optional": {
                "model": (client.model_choices(),),
                "custom_model": ("STRING", {"default": "", "tooltip": "Overrides model. Enter an OpenRouter model ID, including its provider prefix."}),
                "image": ("IMAGE", {"tooltip": "Optional image or image batch. Choose a vision-capable model when connected."}),
                "audio": ("AUDIO", {"tooltip": "Sends a compressed listening copy to an audio-input model."}),
                "excerpt_seconds": ("INT", {"forceInput": True, "min": 1, "tooltip": "Select a continuous passage of this length for the concept."}),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, model=None):
        # Live catalog and saved/custom IDs must survive server restarts/catalog changes.
        # Only model validation is bypassed; ComfyUI still validates other inputs.
        return model is None or isinstance(model, str) or "Model must be a string."

    RETURN_TYPES = ("STRING", "STRING", "STRING", "FLOAT", "STRING")
    RETURN_NAMES = ("text", "model_used", "metadata_json", "excerpt_start_seconds", "shot_prompt")
    FUNCTION = "run"
    CATEGORY = "Coda/OpenRouter"
    OUTPUT_NODE = True

    def run(self, prompt, system_prompt="", model=client.DEFAULT_MODEL, custom_model="", max_tokens=2048, timeout_seconds=120,
            seed=0, image=None, audio=None, excerpt_seconds=None):
        # seed is a ComfyUI cache control, not a reproducibility promise from the model.
        duration = listening.recording_duration(audio) if audio is not None else 0
        if excerpt_seconds is not None:
            if audio is None:
                raise client.OpenRouterError("Connect a song to choose a passage for this concept.")
            instructions = listening.selection_instructions(prompt, duration, excerpt_seconds)
            system_prompt = (system_prompt + "\n" + instructions).strip()
        result = client.generate(prompt, system_prompt, model, custom_model, max_tokens, timeout_seconds,
                                 images=image_data_urls(image), audio_part=listening.audio_input(audio) if audio else None,
                                 json_mode=excerpt_seconds is not None)
        start, preview, shot = 0.0, result[0], result[0]
        if excerpt_seconds is not None:
            start, reason, direction = listening.read_selection(result[0], duration - excerpt_seconds)
            preview = f"{start:.3f} s to {start + excerpt_seconds:.3f} s\n{reason}\n\n{direction}"
            shot = f"{prompt.strip()}\nAnimate the supplied opening frame as one continuous shot. {direction}"
        return {"ui": {"text": [preview]}, "result": (*result, float(start), shot)}


class OpenRouterModels:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"refresh": ("BOOLEAN", {"default": False})},
                "optional": {"kind": (["text", "video", "audio", "dialogue", "speaking_video"],)}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("model_ids", "models_json")
    FUNCTION = "run"
    CATEGORY = "Coda/OpenRouter"
    OUTPUT_NODE = True

    def run(self, refresh=False, kind="text"):
        models = client.list_models(refresh=refresh) if kind == "text" else list_media(kind, refresh)
        ids = "\n".join(item["id"] for item in models)
        return {"ui": {"text": [ids]}, "result": (ids, json.dumps(models, ensure_ascii=False))}


class OpenRouterBalance:
    """Display-only canvas widget. No queue or generation is needed."""
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"
    RETURN_TYPES = ()

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    def run(self):
        return ()


NODE_CLASS_MAPPINGS = {"CodaScreenCharacter": ScreenCharacter, "CodaOnScreenDialogue": OnScreenDialogue,
                       "CodaFrameStack": FrameStack,
                       "CodaMixDialogueSoundtrack": MixDialogueSoundtrack,
                       "CodaOpenRouterBalance": OpenRouterBalance, "CodaOpenRouterPrompt": OpenRouterPrompt, "CodaOpenRouterModels": OpenRouterModels,
                       "CodaOpenRouterVideo": OpenRouterVideo, "CodaOpenRouterSound": OpenRouterSound, "CodaOpenRouterDialogue": OpenRouterDialogue}
NODE_DISPLAY_NAME_MAPPINGS = {"CodaScreenCharacter": "On-Screen Character", "CodaOnScreenDialogue": "OpenRouter On-Screen Dialogue",
                              "CodaFrameStack": "Frame Stack",
                              "CodaMixDialogueSoundtrack": "Mix Dialogue and Soundtrack",
                              "CodaOpenRouterBalance": "OpenRouter Credit Balance", "CodaOpenRouterPrompt": "OpenRouter Prompt", "CodaOpenRouterModels": "OpenRouter List Models",
                              "CodaOpenRouterVideo": "OpenRouter Generate Video", "CodaOpenRouterSound": "OpenRouter Generate Sound",
                              "CodaOpenRouterDialogue": "OpenRouter Character Dialogue"}
