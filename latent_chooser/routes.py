"""Client-scoped chooser endpoints. No worker thread touches aiohttp state."""

from aiohttp import web
from server import PromptServer

from .selection import SelectionError, registry


async def pending(request):
    client_id = request.query.get("client_id")
    if not client_id:
        return web.json_response({"error": "client_id is required."}, status=400)
    return web.json_response({"requests": registry.pending(client_id)},
                             headers={"Cache-Control": "no-store"})


async def respond(request):
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Expected a JSON object."}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not isinstance(body.get("client_id"), str):
        return web.json_response({"error": "token and client_id are required strings."}, status=400)
    try:
        registry.submit(body["token"], body["client_id"], body.get("index"), body.get("cancel", False))
    except SelectionError as error:
        return web.json_response({"error": str(error)}, status=error.status)
    return web.json_response({"ok": True})


def register_routes():
    routes = PromptServer.instance.routes
    routes.get("/latent-chooser/pending")(pending)
    routes.post("/latent-chooser/respond")(respond)
