"""WebSocket manager untuk pipeline & render progress."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # job_id / render_id → list of active connections
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[channel].append(ws)
        logger.debug("WS connected: channel=%s total=%d", channel, len(self._connections[channel]))

    def disconnect(self, channel: str, ws: WebSocket) -> None:
        conns = self._connections.get(channel, [])
        if ws in conns:
            conns.remove(ws)
        logger.debug("WS disconnected: channel=%s remaining=%d", channel, len(conns))

    async def broadcast(self, channel: str, data: dict) -> None:
        conns = list(self._connections.get(channel, []))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)


manager = ConnectionManager()

# Loop asyncio utama — di-set saat aplikasi startup agar emit_* bisa dipanggil
# dari worker thread (run_coroutine_threadsafe butuh referensi loop eksplisit,
# bukan get_event_loop() yang deprecated dari thread non-async di Python 3.10+).
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def _emit(data: dict, channel: str) -> None:
    loop = _main_loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(manager.broadcast(channel, data), loop)


def emit_progress(channel: str, step: int, total: int, label: str, detail: str = "") -> None:
    try:
        _emit({"step": step, "total": total, "label": label,
               "detail": detail, "done": False, "error": ""}, channel)
    except Exception as exc:
        logger.warning("emit_progress failed: %s", exc)


def emit_done(channel: str) -> None:
    try:
        _emit({"done": True, "error": ""}, channel)
    except Exception as exc:
        logger.warning("emit_done failed: %s", exc)


def emit_error(channel: str, message: str) -> None:
    try:
        _emit({"done": True, "error": message}, channel)
    except Exception as exc:
        logger.warning("emit_error failed: %s", exc)
