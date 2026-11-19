"""Thread-safe handoff between ComfyUI's worker and the aiohttp server."""

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable
import time
import uuid


class SelectionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class SelectionCancelled(Exception):
    pass


@dataclass
class Selection:
    token: str
    node_id: str
    client_id: str
    batch_size: int
    images: list[dict]
    event: Event = field(default_factory=Event)
    index: int | None = None
    cancelled: bool = False

    def payload(self) -> dict:
        return {
            "token": self.token,
            "node_id": self.node_id,
            "batch_size": self.batch_size,
            "images": self.images,
        }


class SelectionRegistry:
    def __init__(self):
        self._lock = Lock()
        self._pending: dict[str, Selection] = {}

    def create(self, node_id: str, client_id: str, images: list[dict]) -> Selection:
        if not images:
            raise ValueError("Cannot choose from an empty batch.")
        selection = Selection(uuid.uuid4().hex, str(node_id), client_id, len(images), images)
        with self._lock:
            self._pending[selection.token] = selection
        return selection

    def pending(self, client_id: str) -> list[dict]:
        with self._lock:
            return [s.payload() for s in self._pending.values()
                    if s.client_id == client_id and not s.event.is_set()]

    def submit(self, token: str, client_id: str, index=None, cancel=False) -> None:
        with self._lock:
            selection = self._pending.get(token)
            if selection is None or selection.client_id != client_id:
                raise SelectionError("This chooser request is no longer available.", 404)
            if selection.event.is_set():
                raise SelectionError("This chooser request has already been answered.", 409)
            if type(cancel) is not bool:
                raise SelectionError("cancel must be a boolean.")
            if not cancel and (type(index) is not int or not 0 <= index < selection.batch_size):
                raise SelectionError("index must be an integer within the batch.")
            selection.cancelled = cancel
            selection.index = None if cancel else index
            selection.event.set()

    def discard(self, selection: Selection) -> None:
        with self._lock:
            self._pending.pop(selection.token, None)

    def wait(self, selection: Selection, check_interrupt: Callable[[], None],
             timeout: float = 0) -> int:
        deadline = time.monotonic() + timeout if timeout > 0 else None
        while True:
            check_interrupt()
            if selection.event.wait(0.1):
                check_interrupt()
                if selection.cancelled:
                    raise SelectionCancelled("Image selection cancelled.")
                return selection.index
            if deadline is not None and time.monotonic() >= deadline:
                raise SelectionCancelled("Image selection timed out.")


registry = SelectionRegistry()
