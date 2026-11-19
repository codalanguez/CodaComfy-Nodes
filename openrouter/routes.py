"""Public model catalog only; no browser-side access to the API key."""

import asyncio
from ipaddress import ip_address
from aiohttp import web
from server import PromptServer
from .client import OpenRouterError, list_models, remaining_credit
from .media import list_media


@PromptServer.instance.routes.get("/coda-tools/openrouter/balance")
async def balance(request):
    # The server can listen on the LAN. Keep private billing information local.
    if not request.remote or not ip_address(request.remote).is_loopback:
        raise web.HTTPForbidden(text="Open the local ComfyUI instance to view the balance.")
    try:
        result = await asyncio.to_thread(remaining_credit)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    except OpenRouterError as exc:
        return web.json_response({"error": str(exc)}, status=502, headers={"Cache-Control": "no-store"})


@PromptServer.instance.routes.get("/coda-tools/openrouter/models")
async def models(request):
    try:
        kind = request.query.get("kind", "text")
        refresh = request.query.get("refresh") == "1"
        if kind not in ("text", "video", "audio", "dialogue", "speaking_video"):
            return web.json_response({"error": "Unknown model catalog."}, status=400)
        if kind == "text":
            catalog = await asyncio.to_thread(list_models, refresh)
        else:
            catalog = await asyncio.to_thread(list_media, kind, refresh)
        return web.json_response({"models": catalog}, headers={"Cache-Control": "no-store"})
    except OpenRouterError as exc:
        return web.json_response({"error": str(exc)}, status=502)
