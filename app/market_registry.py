"""行情版本仓库：按版本冻结、绑定与复用行情状态。

不变式：
- 版本一经发布即冻结：MarketSnapshot 本身是 pydantic 冻结模型，仓库只
  增不改；重复发布同一版本抛 VERSION_CONFLICT，绝不覆盖。
- 读路径在锁内取出对象引用后立即释放锁；快照不可变，因此后续定价全程
  无锁且不会读到“被中途改动的半更新行情”。
- 引用不存在的版本抛 VERSION_NOT_FOUND（请求级失败，区别于逐笔失败）。
"""
from __future__ import annotations

import threading

from .errors import ErrorCode, PricingError
from .models import MarketSnapshot


class MarketRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, MarketSnapshot] = {}

    def publish(self, snapshot: MarketSnapshot) -> None:
        """冻结发布一个行情版本；版本已存在则整体拒绝（不覆盖）。"""
        with self._lock:
            if snapshot.snapshot_id in self._snapshots:
                existing = self._snapshots[snapshot.snapshot_id]
                if existing == snapshot:
                    # 幂等：同一版本、同一份内容重复发布视为无操作
                    return
                raise PricingError(
                    ErrorCode.VERSION_CONFLICT,
                    f"行情版本 {snapshot.snapshot_id!r} 已存在且内容不同，"
                    f"版本不可变，拒绝覆盖",
                    {"snapshot_id": snapshot.snapshot_id})
            self._snapshots[snapshot.snapshot_id] = snapshot

    def get(self, version: str) -> MarketSnapshot:
        """读取一个已发布版本；不存在抛 VERSION_NOT_FOUND。"""
        with self._lock:
            snapshot = self._snapshots.get(version)
        if snapshot is None:
            raise PricingError(
                ErrorCode.VERSION_NOT_FOUND,
                f"行情版本 {version!r} 不存在，请先发布再引用",
                {"snapshot_id": version})
        return snapshot

    def versions(self) -> list[str]:
        with self._lock:
            return list(self._snapshots)
