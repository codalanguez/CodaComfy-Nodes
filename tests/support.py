"""Small ComfyUI adapter; real torch, Pillow, and aiohttp remain in use."""

import importlib
from pathlib import Path
from queue import Queue
import sys
import tempfile
from threading import Event
from types import ModuleType, SimpleNamespace

import torch
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("latent_chooser")
package.__path__ = [str(ROOT / "latent_chooser")]
sys.modules[package.__name__] = package


class InterruptProcessingException(Exception):
    pass


class FakeServer:
    def __init__(self):
        self.client_id = "browser-test"
        self.routes = web.RouteTableDef()
        self.events = Queue()

    def send_sync(self, name, payload, client_id):
        self.events.put({"type": name, "data": payload, "client_id": client_id})


server = FakeServer()
interrupt = Event()
test_temp_root = ROOT / ".test-tmp"
test_temp_root.mkdir(exist_ok=True)
temp = tempfile.TemporaryDirectory(prefix="latent_chooser_tests_", dir=test_temp_root)


def check_interrupt():
    if interrupt.is_set():
        raise InterruptProcessingException()


sys.modules["server"] = SimpleNamespace(PromptServer=SimpleNamespace(instance=server))
sys.modules["folder_paths"] = SimpleNamespace(get_temp_directory=lambda: temp.name)
sys.modules["nodes"] = SimpleNamespace(before_node_execution=check_interrupt)
sys.modules["comfy"] = ModuleType("comfy")
sys.modules["comfy.model_management"] = SimpleNamespace(InterruptProcessingException=InterruptProcessingException)
selection = importlib.import_module("latent_chooser.selection")
chooser = importlib.import_module("latent_chooser.chooser")
routes = importlib.import_module("latent_chooser.routes")


class SyntheticVAE:
    def __init__(self):
        self.calls = []

    def decode(self, samples):
        self.calls.append(samples.shape[0])
        value = float(samples[0, 0, 0, 0])
        x = torch.linspace(0, 1, 96).view(1, 1, 96).expand(1, 96, 96)
        y = x.transpose(1, 2)
        return torch.stack(((x + value * .13) % 1, (y + value * .23) % 1,
                            torch.full_like(x, (value * .37) % 1)), dim=-1)


def latents(batch_size=3):
    return {"samples": torch.arange(batch_size, dtype=torch.float32).view(-1, 1, 1, 1).expand(-1, 4, 8, 8).clone()}
