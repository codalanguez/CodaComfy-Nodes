import importlib
import asyncio
import json
from io import BytesIO
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from contextlib import closing

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from support import ROOT

package = ModuleType("lora_preview")
package.__path__ = [str(ROOT / "lora_preview")]
sys.modules[package.__name__] = package
routes = importlib.import_module("lora_preview.routes")
metadata = importlib.import_module("lora_preview.metadata")
nodes = importlib.import_module("lora_preview.nodes")
cache_module = importlib.import_module("lora_preview.cache")


class DiskCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.cache = routes.DiskCache(Path(self.temp.name) / "cache.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_expiry_and_bounds(self):
        with patch.object(cache_module, "time", return_value=100):
            self.assertEqual(self.cache.get_or_compute("key", lambda: b"old"), b"old")
        with patch.object(cache_module, "time", return_value=100 + 86401):
            self.assertEqual(self.cache.get_or_compute("key", lambda: b"new"), b"new")
        self.cache.max_bytes = 10
        for i in range(10):
            self.cache.get_or_compute(str(i), lambda: b"12345")
        with closing(self.cache.connect()) as db:
            size = db.execute("SELECT sum(length(value)) FROM entries").fetchone()[0]
        self.assertLessEqual(size, 10)
        self.assertEqual(self.cache.read("9")[0], b"12345")

    def test_concurrent_requests_compute_once_and_refresh_rejects_old_work(self):
        started, release = Event(), Event()
        def slow():
            started.set()
            self.assertTrue(release.wait(5))
            return b"old"
        compute = Mock(side_effect=slow)
        with ThreadPoolExecutor(4) as pool:
            pending = [pool.submit(self.cache.get_or_compute, "key", compute) for _ in range(4)]
            self.assertTrue(started.wait(5))
            release.set()
            self.assertEqual([p.result() for p in pending], [b"old"] * 4)
        self.assertEqual(compute.call_count, 1)
        self.cache.clear()
        started.clear()
        release.clear()
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.cache.get_or_compute, "key", slow)
            self.assertTrue(started.wait(5))
            self.cache.clear()
            release.set()
            self.assertEqual(pending.result(), b"old")
        self.assertIsNone(self.cache.read("key")[0])

    def test_unavailable_cache_falls_back_to_source(self):
        self.cache.path.write_bytes(b"not a database")
        self.assertEqual(self.cache.get_or_compute("key", lambda: b"still works"), b"still works")


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.root = Path(self.temp.name)
        self.model = self.root / "art.style.safetensors"
        self.model.write_bytes(b"model")
        for name in ("art.style.jpeg", "art.style.preview.png", "art.style-2.webp", "art.style.other.png", "another.png"):
            Image.new("RGB", (640, 320), "navy").save(self.root / name)
        self.sidecar = self.root / "art.style.metadata.json"
        self.sidecar.write_text(json.dumps({"civitai": {"trainedWords": ["artStyle", "ink lines"]}}))
        self.paths = SimpleNamespace(get_filename_list=lambda _: [self.model.name],
                                     get_full_path=lambda _, name: str(self.model) if name == self.model.name else None)
        self.cache = routes.DiskCache(self.root / "cache.sqlite3")
        self.patches = [patch.object(routes, "folder_paths", self.paths), patch.object(nodes, "folder_paths", self.paths),
                        patch.object(routes, "_browser_cache", self.cache)]
        for p in self.patches:
            p.start()
        app = web.Application()
        app.router.add_get("/details", routes.details)
        app.router.add_get("/preview", routes.preview)
        app.router.add_get("/catalog", routes.catalog)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    async def test_preview_matching_and_details(self):
        response = await self.client.get("/details", params={"name": self.model.name})
        self.assertEqual(await response.json(), {"count": 3, "triggers": ["artStyle", "ink lines"]})
        response = await self.client.get("/catalog")
        self.assertEqual((await response.json())["names"], [self.model.name])

    async def test_thumbnail_size_and_invalid_requests(self):
        response = await self.client.get("/preview", params={"name": self.model.name, "size": "128"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, max-age=86400")
        self.assertEqual(Image.open(BytesIO(await response.read())).size, (128, 64))
        for params, expected in [({"name": "../secret"}, 404), ({"name": str(self.model)}, 404),
                                 ({"name": self.model.name, "index": "-1"}, 400),
                                 ({"name": self.model.name, "index": "99"}, 404),
                                 ({"name": self.model.name, "size": "99999"}, 400)]:
            response = await self.client.get("/preview", params=params)
            self.assertEqual(response.status, expected)

    async def test_page_requests_share_catalog_and_refresh_discovers_new_files(self):
        names = [self.model.name]
        self.paths.get_filename_list = Mock(side_effect=lambda _: names[:])
        responses = await asyncio.gather(*(self.client.get("/details", params={"name": self.model.name}) for _ in range(24)))
        self.assertTrue(all(response.status == 200 for response in responses))
        self.assertEqual(self.paths.get_filename_list.call_count, 1)
        added = self.root / "new.safetensors"
        added.write_bytes(b"model")
        names.append(added.name)
        response = await self.client.get("/catalog")
        self.assertEqual((await response.json())["names"], [self.model.name])
        response = await self.client.get("/catalog?refresh=1")
        self.assertEqual((await response.json())["names"], names)
        self.assertEqual(self.paths.get_filename_list.call_count, 2)
        with patch.object(routes, "_catalog_expires", 0):
            await self.client.get("/catalog")
        self.assertEqual(self.paths.get_filename_list.call_count, 3)

    async def test_cached_membership_still_rejects_deleted_models(self):
        await self.client.get("/catalog")
        self.model.unlink()
        response = await self.client.get("/details", params={"name": self.model.name})
        self.assertEqual(response.status, 404)

    async def test_known_previews_do_not_wait_for_expired_or_refreshing_catalog(self):
        await self.client.get("/details", params={"name": self.model.name})
        started, release = Event(), Event()

        def slow_catalog(_):
            started.set()
            if not release.wait(3):
                raise TimeoutError("Test catalog not released")
            return [self.model.name]

        self.paths.get_filename_list = Mock(side_effect=slow_catalog)
        with patch.object(routes, "_catalog_expires", 0):
            response = await asyncio.wait_for(self.client.get("/preview", params={"name": self.model.name}), .5)
            self.assertEqual(response.status, 200)
            self.assertFalse(started.is_set(), "Known previews must not trigger a library rescan")
            refresh = asyncio.create_task(self.client.get("/catalog?refresh=1"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                for path in ("/preview", "/details"):
                    response = await asyncio.wait_for(self.client.get(path, params={"name": self.model.name}), .5)
                    self.assertEqual(response.status, 200)
                self.model.unlink()
                response = await asyncio.wait_for(self.client.get("/details", params={"name": self.model.name}), .5)
                self.assertEqual(response.status, 404, "Cached names must still check file existence")
            finally:
                release.set()
                await refresh

    async def test_missing_and_corrupt_previews(self):
        for path in routes.find_previews(self.model):
            path.unlink()
        response = await self.client.get("/preview", params={"name": self.model.name})
        self.assertEqual(response.status, 404)
        (self.root / "art.style.png").write_text("not an image")
        await self.client.get("/catalog?refresh=1")
        response = await self.client.get("/preview", params={"name": self.model.name})
        self.assertEqual(response.status, 404)

    async def test_disk_cache_survives_recreation_without_source_reads(self):
        params = {"name": self.model.name}
        expected_details = await (await self.client.get("/details", params=params)).json()
        expected_image = await (await self.client.get("/preview", params=params)).read()
        routes._browser_cache = routes.DiskCache(self.cache.path)
        with patch.object(routes, "find_previews", side_effect=AssertionError("rescanned")), \
             patch.object(routes, "read_triggers", side_effect=AssertionError("reread metadata")), \
             patch.object(routes, "thumbnail", side_effect=AssertionError("decoded again")):
            self.assertEqual(await (await self.client.get("/details", params=params)).json(), expected_details)
            self.assertEqual(await (await self.client.get("/preview", params=params)).read(), expected_image)

    async def test_refresh_updates_triggers_and_images_and_missing_results(self):
        params = {"name": self.model.name}
        old_details = await (await self.client.get("/details", params=params)).json()
        old_image = await (await self.client.get("/preview", params=params)).read()
        self.sidecar.write_text(json.dumps({"trainedWords": ["changed"]}))
        Image.new("RGB", (100, 100), "red").save(self.root / "art.style.jpeg")
        self.assertEqual(await (await self.client.get("/details", params=params)).json(), old_details)
        self.assertEqual(await (await self.client.get("/preview", params=params)).read(), old_image)
        await self.client.get("/catalog?refresh=1")
        self.assertEqual((await (await self.client.get("/details", params=params)).json())["triggers"], ["changed"])
        self.assertNotEqual(await (await self.client.get("/preview", params=params)).read(), old_image)
        for path in routes.find_previews(self.model):
            path.unlink()
        await self.client.get("/catalog?refresh=1")
        self.assertEqual((await self.client.get("/preview", params=params)).status, 404)
        with patch.object(routes, "find_previews", side_effect=AssertionError("rescanned missing preview")):
            self.assertEqual((await self.client.get("/preview", params=params)).status, 404)
        Image.new("RGB", (100, 100), "green").save(self.root / "art.style.jpeg")
        await self.client.get("/catalog?refresh=1")
        self.assertEqual((await self.client.get("/preview", params=params)).status, 200)

    async def test_trigger_sources_and_no_inferred_tags(self):
        self.sidecar.write_text("invalid JSON")
        header = json.dumps({"__metadata__": {"ss_trigger_words": '["embedded", "trigger"]'}}).encode()
        self.model.write_bytes(struct.pack("<Q", len(header)) + header)
        self.assertEqual(metadata.read_triggers(self.model), ["embedded", "trigger"])
        self.assertEqual(metadata.extract_triggers({"ss_tag_frequency": {"1girl": 40}}), [])
        self.assertEqual(metadata.extract_triggers({"trainedWords": ["a", "a"], "activation text": "b"}), ["a", "b"])

    async def test_stack_simple_advanced_chaining_and_triggers(self):
        node = nodes.CodaLoraStackPreview()
        entries = json.dumps([{"name": self.model.name, "strength": .7, "clip_strength": .2}])
        result, triggers = node.stack(True, "simple", entries)
        self.assertEqual(result, [(self.model.name, .7, .7)])
        self.assertEqual(triggers, "artStyle, ink lines")
        upstream = [(self.model.name, .1, .2)]
        result, triggers = node.stack(True, "advanced", entries, upstream)
        self.assertEqual(result, upstream + [(self.model.name, .7, .2)])
        self.assertEqual(upstream, [(self.model.name, .1, .2)])
        self.assertEqual(triggers, "artStyle, ink lines")
        self.assertEqual(node.stack(False, "simple", entries, upstream), (None, ""))

    async def test_disabled_empty_zero_and_bad_stack_entries(self):
        node = nodes.CodaLoraStackPreview()
        entries = json.dumps([{"name": self.model.name, "enabled": False}, {"name": ""}])
        self.assertEqual(node.stack(True, "simple", entries), ([], ""))
        entries = json.dumps([{"name": self.model.name, "strength": 0}])
        self.assertEqual(node.stack(True, "simple", entries)[1], "")
        for value in ('invalid', '{}', '[1]', json.dumps([{}] * 11),
                      json.dumps([{"name": "missing"}]),
                      json.dumps([{"name": self.model.name, "strength": float("nan")}])):
            with self.assertRaises(ValueError):
                node.stack(True, "simple", value)

if __name__ == "__main__":
    unittest.main()

