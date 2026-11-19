"""Coda's ComfyUI tools: latent selection and OpenRouter."""

from .latent_chooser.chooser import LatentImageChooser
from .latent_chooser.routes import register_routes
from .openrouter.nodes import NODE_CLASS_MAPPINGS as ROUTER_NODES
from .openrouter.nodes import NODE_DISPLAY_NAME_MAPPINGS as ROUTER_NAMES
from .openrouter import routes as _openrouter_routes
from .lora_preview.nodes import CodaLoraStackPreview
from .lora_preview.routes import register_routes as register_lora_routes

NODE_CLASS_MAPPINGS = {
    "LatentImageChooser": LatentImageChooser,
    "CodaLoraStackPreview": CodaLoraStackPreview,
    **ROUTER_NODES,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LatentImageChooser": "Latent Image Chooser (Pause)",
    "CodaLoraStackPreview": "LoRA Stack (Preview + Triggers)",
    **ROUTER_NAMES,
}
WEB_DIRECTORY = "./web"
register_routes()
register_lora_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
