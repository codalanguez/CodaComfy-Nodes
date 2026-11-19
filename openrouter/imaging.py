"""Encode ComfyUI IMAGE batches as inline PNGs for vision requests."""

import base64
import io

import numpy as np
from PIL import Image
import torch

from .client import OpenRouterError


def image_data_urls(image):
    if image is None:
        return []
    if (not isinstance(image, torch.Tensor) or image.is_nested or image.ndim != 4
            or any(size < 1 for size in image.shape) or image.shape[-1] not in (1, 3, 4)):
        raise OpenRouterError("Expected an IMAGE batch [B, H, W, C] with 1, 3, or 4 channels.")
    urls = []
    for frame in image:
        pixels = frame.detach().to(device="cpu", dtype=torch.float32)
        if not torch.isfinite(pixels).all():
            raise OpenRouterError("The image contains non-finite pixel values.")
        pixels = np.rint(pixels.clamp(0, 1).numpy() * 255).astype(np.uint8)
        if pixels.shape[-1] == 1:
            pixels = pixels[..., 0]
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="PNG")
        urls.append("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
    return urls
