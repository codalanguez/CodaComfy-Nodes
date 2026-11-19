"""Only serve sidecar images belonging to registered LoRA filenames."""
import asyncio
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
import json
import sqlite3
from threading import Lock
from time import monotonic

from aiohttp import web
from PIL import Image, ImageOps, UnidentifiedImageError
import folder_paths
from server import PromptServer
from .metadata import read_triggers
from .cache import DiskCache

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
CATALOG_TTL = 60
_catalog_lock = Lock()
_catalog_names = ()
_catalog_members = frozenset()
_catalog_expires = 0
_catalog_provider = None
_browser_cache = None


def browser_cache():
    global _browser_cache
    if _browser_cache is None:
        user_dir = getattr(folder_paths, "get_user_directory", lambda: str(Path(__file__).resolve().parents[3] / "user"))()
        _browser_cache = DiskCache(Path(user_dir) / "coda" / "lora-browser-v1.sqlite3")
    return _browser_cache


def model_key(model):
    stat = model.stat()
    return json.dumps([str(model.absolute()), stat.st_mtime_ns, stat.st_size])


def cached_previews(model, key):
    data = browser_cache().get_or_compute("paths:" + key,
        lambda: json.dumps([str(p) for p in find_previews(model)]).encode())
    return [Path(p) for p in json.loads(data)]


def catalog_snapshot(refresh=False):
    """Validate the library once per minute, not once for every thumbnail.

    ComfyUI's filename cache still stats every registered subdirectory on each
    call. Serialize cold requests so a page of thumbnails shares one scan.
    """
    global _catalog_names, _catalog_members, _catalog_expires, _catalog_provider
    with _catalog_lock:
        if refresh or _catalog_provider is not folder_paths or monotonic() >= _catalog_expires:
            names = tuple(folder_paths.get_filename_list("loras"))
            _catalog_names, _catalog_members = names, frozenset(names)
            _catalog_provider = folder_paths
            _catalog_expires = monotonic() + CATALOG_TTL
        return _catalog_names, _catalog_members


def find_previews(model):
    """Support stem.png, stem.preview.png and numbered sidecar variants."""
    model = Path(model).resolve()
    bases = (model.stem.casefold(), model.name.casefold())
    matches = []
    for path in model.parent.iterdir():
        if path.suffix.casefold() not in IMAGE_EXTENSIONS or not path.is_file():
            continue
        # Do not follow image symlinks outside the model's own directory.
        if path.resolve().parent != model.parent:
            continue
        stem = path.stem.casefold()
        for base in bases:
            if stem == base or re.fullmatch(re.escape(base) + r"[._-](?:(?:preview|thumb|thumbnail|image)(?:[._-]?\d+)?|\d+)", stem):
                matches.append(path)
                break
    return sorted(matches, key=lambda p: (p.stem.casefold() not in bases, p.name.casefold()))


def resolve_model(name):
    # Membership disallows arbitrary paths, traversal and non-LoRA files.
    # Known names need only their own existence check. Revalidating every
    # library directory here stalls all previews once the catalog TTL expires,
    # and can make them wait behind an explicit catalog refresh. Immutable
    # membership snapshots are replaced by catalog_snapshot(), never mutated.
    known = _catalog_provider is folder_paths and name in _catalog_members
    if not name or (not known and name not in catalog_snapshot()[1]):
        raise web.HTTPNotFound(text="Unknown LoRA")
    path = folder_paths.get_full_path("loras", name)
    if not path or not Path(path).is_file():
        raise web.HTTPNotFound(text="LoRA file not found")
    return Path(path)


async def catalog(request):
    refresh = request.query.get("refresh") == "1"
    def read():
        names, _ = catalog_snapshot(refresh)
        if refresh:
            try:
                browser_cache().clear()
            except (OSError, sqlite3.Error):
                pass
            thumbnail.cache_clear()
        return names
    names = await asyncio.to_thread(read)
    return web.json_response({"names": names}, headers={"Cache-Control": "no-store"})


def preview_paths(name):
    try:
        model = resolve_model(name)
        return cached_previews(model, model_key(model))
    except OSError:
        raise web.HTTPNotFound(text="Preview folder is unavailable")


async def details(request):
    def read():
        model = resolve_model(request.query.get("name", ""))
        key = model_key(model)
        data = browser_cache().get_or_compute("details:" + key,
            lambda: json.dumps({"count": len(cached_previews(model, key)), "triggers": read_triggers(model)}).encode())
        return json.loads(data)
    try:
        data = await asyncio.to_thread(read)
    except OSError:
        raise web.HTTPNotFound(text="LoRA folder is unavailable")
    return web.json_response(data, headers={"Cache-Control": "no-store"})


@lru_cache(maxsize=192)
def thumbnail(path, mtime_ns, file_size, size):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((size, size))
        image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()


async def preview(request):
    try:
        index = int(request.query.get("index", "0"))
        size = int(request.query.get("size", "512"))
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid image index or size")
    if size not in (128, 512, 1024) or index < 0:
        raise web.HTTPBadRequest(text="Invalid image index or size")
    def read():
        model = resolve_model(request.query.get("name", ""))
        key = model_key(model)
        def render():
            paths = cached_previews(model, key)
            if index >= len(paths):
                return b""  # Missing previews are cached too.
            path = paths[index]
            try:
                if path.resolve().parent != model.resolve().parent:
                    return b""
                stat = path.stat()
                return thumbnail(str(path), stat.st_mtime_ns, stat.st_size, size)
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                return b""
        return browser_cache().get_or_compute(f"image:{index}:{size}:" + key, render)
    try:
        data = await asyncio.to_thread(read)
    except OSError:
        raise web.HTTPNotFound(text="Preview folder is unavailable")
    if not data:
        raise web.HTTPNotFound(text="No readable preview attached",
                               headers={"Cache-Control": "private, max-age=86400"})
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


def register_routes():
    routes = PromptServer.instance.routes
    routes.get("/coda/loras")(catalog)
    routes.get("/coda/loras/details")(details)
    routes.get("/coda/loras/preview")(preview)
