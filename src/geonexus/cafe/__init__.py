"""CAFE — Computation-to-Data Pushdown Engine.

参考 CAFE（Xu & Bai et al., 2018）"计算移动至数据端"范式：
- :class:`CentralServer` 接收任务，按数据本地性/算力余量/合规策略路由
- :class:`WorkerNode` 管理本地数据分片，在沙箱中执行下推代码，返回聚合结果
- :mod:`protocol` 定义 pushdown 协商/执行/结果回传的协议

相对 geo.execute 的设计差异：这里任务粒度是"代码下推"而非"技能调用"，
即把计算逻辑推送到数据所在 Worker 执行（零原始数据下载）。
"""

from .central import CentralServer, RoutingDecision, TaskRouter
from .protocol import PushdownRequest, PushdownResult, TaskStatus
from .worker import WorkerNode, worker_function

__all__ = [
    "CentralServer",
    "WorkerNode",
    "worker_function",
    "PushdownRequest",
    "PushdownResult",
    "TaskStatus",
    "RoutingDecision",
    "TaskRouter",
]