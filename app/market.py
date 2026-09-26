"""行情状态版本化存取与合约解析。

- 快照不可变（frozen model），发布后内容永不改变；
  版本到快照的映射在锁内原子更新，读者只能拿到完整快照，
  不会读到被中途改动的半更新行情。
- 同一请求内所有合约共享同一份快照（请求进入时解析一次）。
- 引用同一版本的多个请求（无论先后或并发）解析出完全一致的合约，
  因此定价结果完全一致。
"""
from __future__ import annotations

import threading

from .errors import ErrorCode, PricingError
from .models import Contract, MarketPrecedence, MarketSnapshot


class MarketStore:
    """线程安全的行情快照版本库。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._next_id = 0

    def publish(self, spot: float, risk_free_rate: float,
                dividend_yield: float = 0.0) -> MarketSnapshot:
        """发布新快照并返回（含分配的版本号）。版本号单调递增。"""
        with self._lock:
            self._next_id += 1
            snapshot = MarketSnapshot(version=f"v{self._next_id}", spot=spot,
                                      risk_free_rate=risk_free_rate,
                                      dividend_yield=dividend_yield)
            self._snapshots[snapshot.version] = snapshot
            return snapshot

    def get(self, version: str) -> MarketSnapshot:
        """按版本取快照；版本不存在抛 UNKNOWN_MARKET_VERSION。"""
        snapshot = self._snapshots.get(version)
        if snapshot is None:
            raise PricingError(
                ErrorCode.UNKNOWN_MARKET_VERSION,
                f"行情版本不存在: {version!r}",
                {"version": version,
                 "available": sorted(self._snapshots)})
        return snapshot


def resolve_contract(
    c: Contract,
    snapshot: MarketSnapshot | None = None,
    precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST,
) -> Contract:
    """按优先级把行情状态合并进合约，返回新合约（原对象不变）。

    - MARKET_FIRST：行情覆盖合约的 spot/risk_free_rate/dividend_yield。
    - CONTRACT_FIRST：合约自带取值优先，行情仅填补合约缺失的字段。
    - 未绑定行情时 dividend_yield 缺失取文档化默认 0.0；
      spot/risk_free_rate 缺失留待引擎校验报 VALIDATION。
    """
    updates: dict[str, float] = {}
    if snapshot is not None:
        if precedence is MarketPrecedence.MARKET_FIRST:
            updates = {"spot": snapshot.spot,
                       "risk_free_rate": snapshot.risk_free_rate,
                       "dividend_yield": snapshot.dividend_yield}
        else:
            if c.spot is None:
                updates["spot"] = snapshot.spot
            if c.risk_free_rate is None:
                updates["risk_free_rate"] = snapshot.risk_free_rate
            if c.dividend_yield is None:
                updates["dividend_yield"] = snapshot.dividend_yield
    if c.dividend_yield is None and "dividend_yield" not in updates:
        updates["dividend_yield"] = 0.0
    return c.model_copy(update=updates) if updates else c
