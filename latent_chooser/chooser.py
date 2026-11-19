"""Decode previews, wait for a human choice, then pass the original latent on."""

import logging
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image
import torch

import folder_paths
import nodes
from comfy.model_management import InterruptProcessingException
from server import PromptServer

from .selection import SelectionCancelled, registry

log = logging.getLogger(__name__)


def select_latent(latent: dict, index: int) -> dict:
    """Keep seed identity and ComfyUI's repeating/broadcast noise-mask semantics."""
    samples = latent["samples"]
    if type(index) is not int or not 0 <= index < samples.shape[0]:
        raise ValueError("Selection index is outside the latent batch.")
    result = latent.copy()
    result["samples"] = samples[index:index + 1].clone()
    result["batch_index"] = list(latent.get("batch_index", range(samples.shape[0]))[index:index + 1])
    if "noise_mask" in latent:
        mask = latent["noise_mask"]
        if mask.shape[0] < 1:
            raise ValueError("noise_mask cannot have an empty batch.")
        mask_index = index % mask.shape[0]
        result["noise_mask"] = mask[mask_index:mask_index + 1].clone()
    return result


def decode_image(vae, samples):
    # Anima/Qwen still images can use a singleton temporal axis. Adapt only
    # the decode input, leaving the selected latent's original layout intact.
    latent_dim = getattr(vae, "latent_dim", None)
    if latent_dim == 3 and samples.ndim == 4:
        samples = samples.unsqueeze(2)
    elif latent_dim == 2 and samples.ndim == 5:
        samples = samples[:, :, 0]
    with torch.inference_mode():
        images = vae.decode(samples)
    if images.ndim == 5 and images.shape[1] == 1:
        images = images[:, 0]
    if images.ndim != 4 or images.shape[0] != 1 or images.shape[-1] not in (1, 3, 4):
        raise ValueError("The VAE must decode each latent to one BHWC still image.")
    return images


class LatentImageChooser:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latents": ("LATENT",),
                "vae": ("VAE",),
                "preview_size": ("INT", {"default": 512, "min": 128, "max": 2048, "step": 64,
                    "tooltip": "Thumbnail longest edge. The large popup always uses the full decoded resolution."}),
                "timeout_seconds": ("INT", {"default": 0, "min": 0, "max": 86400,
                    "tooltip": "0 waits indefinitely. A timeout cancels; it never picks for you."}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("LATENT", "IMAGE", "INT")
    RETURN_NAMES = ("selected_latent", "selected_image", "selected_index")
    FUNCTION = "choose"
    CATEGORY = "latent/interactive"
    OUTPUT_NODE = True
    DESCRIPTION = "Preview any nonempty still-image latent batch and pause until you choose one."

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Never silently reuse a prior human choice, even when upstream is cached.
        return float("nan")

    def choose(self, latents, vae, preview_size=512, timeout_seconds=0, unique_id=""):
        samples = latents.get("samples")
        if (not isinstance(samples, torch.Tensor) or samples.is_nested
                or samples.ndim not in (4, 5) or any(size < 1 for size in samples.shape)):
            shape = tuple(samples.shape) if isinstance(samples, torch.Tensor) and not samples.is_nested else type(samples).__name__
            raise ValueError(f"Expected a nonempty still-image LATENT [B, C, H, W] or [B, C, 1, H, W]; got {shape}.")
        if samples.ndim == 5 and samples.shape[2] != 1:
            raise ValueError(f"Only single-frame image latents are supported; got {samples.shape[2]} frames in {tuple(samples.shape)}.")
        server = PromptServer.instance
        client_id = server.client_id
        if not client_id:
            raise ValueError("Interactive selection needs a browser client. Queue from ComfyUI, or supply client_id through the API.")
        selection = None
        # A private directory prevents name collisions and is removed on every exit path.
        with tempfile.TemporaryDirectory(prefix="latent_chooser_", dir=folder_paths.get_temp_directory()) as directory:
            try:
                previews = []
                for index in range(samples.shape[0]):
                    nodes.before_node_execution()
                    images = decode_image(vae, samples[index:index + 1])
                    pixels = images[0].detach().to(device="cpu", dtype=torch.float32).clamp(0, 1).numpy()
                    pixels = np.rint(pixels * 255).astype(np.uint8)
                    if pixels.shape[-1] == 1:
                        pixels = pixels[..., 0]
                    image = Image.fromarray(pixels)
                    # Preserve the decoded pixels before resizing the grid thumbnail.
                    # Both files share the request's temporary-directory lifetime.
                    full_filename = f"{index:06d}_full.png"
                    image.save(Path(directory) / full_filename)
                    image.thumbnail((preview_size, preview_size), Image.Resampling.LANCZOS)
                    filename = f"{index:06d}.png"
                    image.save(Path(directory) / filename)
                    previews.append({
                        "filename": filename, "subfolder": Path(directory).name, "type": "temp",
                        "full_size": {"filename": full_filename, "subfolder": Path(directory).name, "type": "temp"},
                    })
                    del images, pixels, image
                nodes.before_node_execution()
                selection = registry.create(str(unique_id), client_id, previews)
                log.info("Latent chooser %s: waiting for a choice from %d images", unique_id, len(previews))
                server.send_sync("latent-chooser-request", selection.payload(), client_id)
                index = registry.wait(selection, nodes.before_node_execution, timeout_seconds)
                selected = select_latent(latents, index)
                nodes.before_node_execution()
                image = decode_image(vae, selected["samples"])
                return (selected, image, index)
            except SelectionCancelled as error:
                log.info("Latent chooser %s: %s", unique_id, error)
                raise InterruptProcessingException() from error
            finally:
                if selection is not None:
                    registry.discard(selection)
                    server.send_sync("latent-chooser-closed", {"token": selection.token}, client_id)
