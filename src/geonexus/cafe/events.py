"""GeoMCP SSE 事件推送 — 执行进度实时流。

为 GeoMCP geo.execute 添加 Server-Sent Events 支持：
客户端通过 GET /events/{request_id} 订阅任务进度，
服务端在执行阶段推送 checkpoints。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)

# 全局事件总线（单进程内存实现，可替换为 Redis pub/sub）
_event_streams: dict[str, list[asyncio.Queue]] = defaultdict(list)
_event_history: dict[str, list[dict[str, Any]]] = defaultdict(list)


def push_event(request_id: str, event_type: str, data: dict[str, Any]) -> None:
    """向所有订阅 request_id 的 SSE 客户端推送事件。"""
    payload = {
        "request_id": request_id,
        "event": event_type,
        "data": data,
        "timestamp": time.time(),
    }
    # 保存历史（重连时可回放）
    _event_history[request_id].append(payload)
    # 推送给所有等待的消费者
    for queue in _event_streams.get(request_id, []):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(payload)


async def subscribe_events(request_id: str) -> AsyncGenerator[str, None]:
    """/events/{request_id} 的 SSE 生成器。

    用法（FastAPI）:
        @app.get("/events/{request_id}")
        async def events(request_id: str):
            return StreamingResponse(
                subscribe_events(request_id),
                media_type="text/event-stream",
            )
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _event_streams[request_id].append(queue)

    try:
        # 1. 发送历史事件（重连时回放）
        for past in _event_history.get(request_id, []):
            yield f"event: {past['event']}\ndata: {json.dumps(past['data'])}\n\n"

        # 2. 发送初始连接事件
        yield f"event: connected\ndata: {json.dumps({'request_id': request_id})}\n\n"

        # 3. 持续推送新事件
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=300)
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
            except asyncio.TimeoutError:
                yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
    finally:
        _event_streams[request_id].remove(queue)
        if not _event_streams[request_id]:
            del _event_streams[request_id]


def clear_events(request_id: str) -> None:
    """清理指定 request_id 的事件历史和流。"""
    _event_history.pop(request_id, None)
    _event_streams.pop(request_id, None)


# ── Convenience: emit progress during geo.execute ──

def notify_checkpoint(request_id: str, checkpoint_id: str, status: str, message: str = "") -> None:
    """在技能执行过程中发送进度事件。"""
    push_event(request_id, "checkpoint", {
        "id": checkpoint_id,
        "status": status,
        "message": message or f"Checkpoint {checkpoint_id}: {status}",
    })


def notify_completion(request_id: str, outputs: dict[str, Any]) -> None:
    """任务完成事件。"""
    push_event(request_id, "completed", {"outputs": outputs})


def notify_error(request_id: str, error: str) -> None:
    """任务失败事件。"""
    push_event(request_id, "error", {"error": error})