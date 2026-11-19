import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from support import routes, selection


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.request = selection.registry.create("9", "http-client", [{}, {}])
        application = web.Application()
        application.router.add_get("/latent-chooser/pending", routes.pending)
        application.router.add_post("/latent-chooser/respond", routes.respond)
        self.client = TestClient(TestServer(application))
        await self.client.start_server()

    async def asyncTearDown(self):
        selection.registry.discard(self.request)
        await self.client.close()

    async def test_http_recovery_and_selection(self):
        response = await self.client.get("/latent-chooser/pending?client_id=http-client")
        self.assertEqual((await response.json())["requests"][0]["token"], self.request.token)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = await self.client.post("/latent-chooser/respond", json={"token": self.request.token, "client_id": "http-client", "index": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual(selection.registry.wait(self.request, lambda: None), 1)

    async def test_invalid_payloads_do_not_unblock(self):
        for body in [[], {}, {"token": self.request.token, "client_id": "http-client", "index": True}, {"token": self.request.token, "client_id": "http-client", "index": 0, "cancel": "false"}]:
            response = await self.client.post("/latent-chooser/respond", json=body)
            self.assertEqual(response.status, 400)
        response = await self.client.post("/latent-chooser/respond", data="not json", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertFalse(self.request.event.is_set())

    async def test_foreign_clients_cannot_recover_or_select(self):
        response = await self.client.get("/latent-chooser/pending?client_id=other-client")
        self.assertEqual((await response.json())["requests"], [])
        response = await self.client.post("/latent-chooser/respond", json={"token": self.request.token, "client_id": "other-client", "index": 0})
        self.assertEqual(response.status, 404)

    async def test_missing_client_id(self):
        response = await self.client.get("/latent-chooser/pending")
        self.assertEqual(response.status, 400)

    async def test_cancel_and_duplicate(self):
        payload = {"token": self.request.token, "client_id": "http-client", "cancel": True}
        response = await self.client.post("/latent-chooser/respond", json=payload)
        self.assertEqual(response.status, 200)
        response = await self.client.post("/latent-chooser/respond", json=payload)
        self.assertEqual(response.status, 409)
        with self.assertRaises(selection.SelectionCancelled):
            selection.registry.wait(self.request, lambda: None)


if __name__ == "__main__":
    unittest.main()
