"""Import the combined entry point against the existing ComfyUI test adapter."""

import importlib.util
from pathlib import Path
import sys
import unittest


class PackTests(unittest.TestCase):
    def test_pack_registers_all_nodes_routes_and_web_assets(self):
        from support import server
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("coda_pack_test", root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertEqual(set(module.NODE_CLASS_MAPPINGS), {"LatentImageChooser", "CodaOpenRouterPrompt", "CodaOpenRouterModels",
                                                          "CodaOpenRouterVideo", "CodaOpenRouterSound", "CodaOpenRouterBalance", "CodaLoraStackPreview",
                                                          "CodaScreenCharacter", "CodaFrameStack", "CodaOnScreenDialogue",
                                                          "CodaMixDialogueSoundtrack", "CodaOpenRouterDialogue"})
        paths = [route.path for route in server.routes]
        for path in ["/latent-chooser/pending", "/coda-tools/openrouter/models", "/coda-tools/openrouter/balance", "/coda/loras", "/coda/loras/details", "/coda/loras/preview"]:
            self.assertIn(path, paths)
        self.assertFalse(any(path.startswith("/claude_node/") for path in paths))
        web = root / module.WEB_DIRECTORY
        for name in ["chooser.js", "gallery.js", "chooser.css", "openrouter.js", "lora_preview.js", "lora_preview.css"]:
            self.assertTrue((web / name).is_file())
