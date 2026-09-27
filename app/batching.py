"""批量定价编排：行情版本绑定与取值优先级。

- 未绑定版本：完全按合约自带取值（兼容既有行为）。
- 绑定版本：先在任何定价之前解析冻结快照（版本不存在 → 整体拒绝，
  不残留部分结果），再逐笔按 precedence 解析并定价；每笔结果
  （成功或失败）回带实际使用的 market_version。
"""
from __future__ import annotations

from .config import resolve_cross_check_tolerance
from .engine import PricingEngine
from .errors import ErrorCode, PricingError
from .market import resolve_market
from .market_registry import MarketRegistry
from .models import (BatchItemResult, BatchPriceResponse, Contract, ItemError,
                     MarketPrecedence, PricingMethod)


def run_batch_pricing(engine: PricingEngine,
                      registry: MarketRegistry | None,
                      contracts: list[Contract],
                      method: PricingMethod,
                      cross_check_tolerance: float | None = None,
                      market_version: str | None = None,
                      precedence: MarketPrecedence = MarketPrecedence.CONTRACT
                      ) -> BatchPriceResponse:
    # 请求级容差在任何逐笔计算之前解析：非法取值整体拒绝，不残留部分结果
    tolerance = resolve_cross_check_tolerance(
        cross_check_tolerance, engine.settings.cross_check_tolerance)
    if market_version is not None:
        # 请求级失败：版本不存在时在任何定价之前整体拒绝
        if registry is None:
            raise PricingError(
                ErrorCode.VERSION_NOT_FOUND,
                f"行情版本 {market_version!r} 不存在，请先发布再引用",
                {"snapshot_id": market_version})
        snapshot = registry.get(market_version)
    else:
        snapshot = None

    if len(contracts) > engine.settings.max_batch_size:
        # 超限整体拒绝，不残留部分计算结果
        raise PricingError(
            ErrorCode.LIMIT_EXCEEDED,
            f"批量规模 {len(contracts)} 超过上限 {engine.settings.max_batch_size}",
            {"size": len(contracts),
             "limit": engine.settings.max_batch_size})

    results: list[BatchItemResult] = []
    for c in contracts:
        resolved, version, mkt_err = resolve_market(c, snapshot, precedence)
        if mkt_err is not None:
            results.append(BatchItemResult(
                contract_id=c.contract_id, ok=False, market_version=version,
                error=mkt_err))
            continue
        try:
            result = engine.price_one(resolved, method, tolerance)
            results.append(BatchItemResult(
                contract_id=c.contract_id, ok=True, result=result,
                market_version=version))
        except PricingError as e:
            # 单笔失败隔离：记录可区分错误，继续处理其余
            results.append(BatchItemResult(
                contract_id=c.contract_id, ok=False, market_version=version,
                error=ItemError(**e.to_dict())))
    succeeded = sum(1 for r in results if r.ok)
    return BatchPriceResponse(results=results, succeeded=succeeded,
                              failed=len(results) - succeeded,
                              market_version=market_version)
