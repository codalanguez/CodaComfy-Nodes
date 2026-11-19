import concurrent.futures
from pathlib import Path
from queue import Empty
import unittest

import numpy as np
from PIL import Image
import torch

from support import chooser, selection, server, temp, interrupt, latents, SyntheticVAE, InterruptProcessingException


class LatentTests(unittest.TestCase):
    def test_original_samples_seed_and_metadata_preserved(self):
        source = latents(3)
        source.update(batch_index=[10, 20, 30], downscale_ratio_spacial=8)
        picked = chooser.select_latent(source, 2)
        self.assertEqual(picked["samples"].shape[0], 1)
        self.assertTrue(torch.equal(picked["samples"], source["samples"][2:3]))
        self.assertEqual(picked["batch_index"], [30])
        self.assertEqual(picked["downscale_ratio_spacial"], 8)
        picked["samples"].zero_()
        self.assertEqual(float(source["samples"][2, 0, 0, 0]), 2)

    def test_missing_seed_index_is_original_position(self):
        self.assertEqual(chooser.select_latent(latents(4), 3)["batch_index"], [3])

    def test_broadcast_full_and_repeating_masks(self):
        source = latents(5)
        for count in [1, 2, 5]:
            with self.subTest(count=count):
                source["noise_mask"] = torch.arange(count).view(-1, 1, 1, 1)
                picked = chooser.select_latent(source, 3)
                self.assertEqual(picked["noise_mask"].shape[0], 1)
                self.assertEqual(picked["noise_mask"].item(), 3 % count)

    def test_invalid_index_rejected(self):
        with self.assertRaises(ValueError):
            chooser.select_latent(latents(), 3)


class NodeTests(unittest.TestCase):
    def setUp(self):
        interrupt.clear()
        server.client_id = "browser-test"
        while True:
            try:
                server.events.get_nowait()
            except Empty:
                break

    def tearDown(self):
        interrupt.clear()

    def run_waiting_node(self, action, batch_size=3, source=None, vae=None):
        vae = vae if vae is not None else SyntheticVAE()
        source = source if source is not None else latents(batch_size)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(chooser.LatentImageChooser().choose, source, vae, 128, 0, "7")
            event = server.events.get(timeout=10)
            self.assertEqual(event["type"], "latent-chooser-request")
            self.assertEqual(event["client_id"], "browser-test")
            self.assertFalse(future.done(), "Node must pause until a response is made")
            descriptors = event["data"]["images"]
            folder = Path(temp.name) / descriptors[0]["subfolder"]
            self.assertEqual(len(list(folder.glob("*.png"))), batch_size * 2)
            action(event["data"])
            try:
                return future.result(timeout=5), vae
            finally:
                self.assertFalse(folder.exists())
                self.assertEqual(selection.registry.pending("browser-test"), [])

    def test_real_wait_resume_and_full_image(self):
        result, vae = self.run_waiting_node(lambda data: selection.registry.submit(data["token"], "browser-test", 2))
        selected, image, index = result
        self.assertEqual(index, 2)
        self.assertTrue(torch.equal(selected["samples"], latents()["samples"][2:3]))
        self.assertEqual(image.shape, (1, 96, 96, 3))
        self.assertEqual(vae.calls, [1, 1, 1, 1])

    def test_single_item_still_waits(self):
        result, _ = self.run_waiting_node(lambda data: selection.registry.submit(data["token"], "browser-test", 0), batch_size=1)
        self.assertEqual(result[2], 0)

    def test_popup_preserves_full_resolution_pixels_with_small_thumbnails(self):
        # Fine detail and a non-square image expose accidental thumbnail reuse.
        pixels = torch.arange(240 * 320 * 3).remainder(256).to(torch.uint8).reshape(240, 320, 3)

        class DetailedVAE:
            def decode(self, samples):
                return pixels.unsqueeze(0).float() / 255

        def inspect_and_select(data):
            try:
                descriptor = data["images"][0]
                folder = Path(temp.name) / descriptor["subfolder"]
                with Image.open(folder / descriptor["filename"]) as thumbnail:
                    self.assertEqual(thumbnail.size, (128, 96))
                full = descriptor["full_size"]
                self.assertEqual(full["subfolder"], descriptor["subfolder"])
                self.assertEqual(full["type"], "temp")
                with Image.open(folder / full["filename"]) as popup:
                    self.assertEqual(popup.size, (320, 240))
                    np.testing.assert_array_equal(np.asarray(popup), pixels.numpy())
                self.assertEqual(selection.registry.pending("browser-test")[0]["images"], data["images"])
            finally:
                selection.registry.submit(data["token"], "browser-test", 0)

        self.run_waiting_node(inspect_and_select, batch_size=1, vae=DetailedVAE())

    def test_anima_single_frame_batch_previews_waits_and_preserves_latent(self):
        class SingleFrameVAE(SyntheticVAE):
            latent_dim = 3

            def decode(self, samples):
                self_shape = tuple(samples.shape)
                if self_shape != (1, 4, 1, 8, 8):
                    raise AssertionError(f"Wrong VAE input: {self_shape}")
                return super().decode(samples[:, :, 0]).unsqueeze(1)

        source = latents(3)
        source["samples"] = source["samples"].unsqueeze(2)
        source["noise_mask"] = torch.arange(3).view(3, 1, 1, 1, 1)
        source["batch_index"] = [10, 20, 30]
        (picked, image, index), vae = self.run_waiting_node(
            lambda data: selection.registry.submit(data["token"], "browser-test", 1),
            source=source, vae=SingleFrameVAE())
        self.assertEqual(index, 1)
        self.assertEqual(picked["samples"].shape, (1, 4, 1, 8, 8))
        self.assertTrue(torch.equal(picked["samples"], source["samples"][1:2]))
        self.assertEqual(picked["noise_mask"].shape, (1, 1, 1, 1, 1))
        self.assertEqual(picked["noise_mask"].item(), 1)
        self.assertEqual(picked["batch_index"], [20])
        self.assertEqual(image.shape, (1, 96, 96, 3))
        self.assertEqual(vae.calls, [1, 1, 1, 1])

    def test_decode_4d_with_temporal_vae(self):
        class TemporalVAE:
            latent_dim = 3

            def decode(self, samples):
                self.shape = tuple(samples.shape)
                return torch.zeros(1, 1, 16, 16, 3)

        vae = TemporalVAE()
        self.assertEqual(chooser.decode_image(vae, latents(1)["samples"]).shape, (1, 16, 16, 3))
        self.assertEqual(vae.shape, (1, 4, 1, 8, 8))

    def test_decode_5d_with_spatial_vae(self):
        class SpatialVAE(SyntheticVAE):
            latent_dim = 2

            def decode(self, samples):
                if samples.ndim != 4:
                    raise AssertionError("Spatial VAE requires 4D input")
                return super().decode(samples)

        self.assertEqual(chooser.decode_image(SpatialVAE(), latents(1)["samples"].unsqueeze(2)).shape,
                         (1, 96, 96, 3))

    def test_multiple_decoded_frames_rejected(self):
        class VideoVAE:
            def decode(self, samples):
                return torch.zeros(1, 2, 16, 16, 3)

        with self.assertRaisesRegex(ValueError, "one BHWC still image"):
            chooser.decode_image(VideoVAE(), latents(1)["samples"])

    def test_stop_interrupts_and_cleans_up(self):
        with self.assertRaises(InterruptProcessingException):
            self.run_waiting_node(lambda data: interrupt.set())

    def test_cancel_interrupts_and_cleans_up(self):
        with self.assertRaises(InterruptProcessingException):
            self.run_waiting_node(lambda data: selection.registry.submit(data["token"], "browser-test", cancel=True))

    def test_decode_failure_cleans_preview_directory(self):
        class BrokenVAE(SyntheticVAE):
            def decode(self, samples):
                if self.calls:
                    raise RuntimeError("decode failed")
                return super().decode(samples)
        with self.assertRaisesRegex(RuntimeError, "decode failed"):
            chooser.LatentImageChooser().choose(latents(), BrokenVAE())
        self.assertEqual(list(Path(temp.name).iterdir()), [])

    def test_headless_empty_and_video_rejected(self):
        node = chooser.LatentImageChooser()
        for source in [{"samples": torch.zeros(0, 4, 8, 8)}, {"samples": torch.zeros(1, 4, 2, 8, 8)}]:
            with self.assertRaises(ValueError):
                node.choose(source, SyntheticVAE())
        server.client_id = None
        with self.assertRaisesRegex(ValueError, "browser client"):
            node.choose(latents(), SyntheticVAE())

    def test_choice_is_never_cached(self):
        self.assertNotEqual(chooser.LatentImageChooser.IS_CHANGED(), chooser.LatentImageChooser.IS_CHANGED())


if __name__ == "__main__":
    unittest.main()
