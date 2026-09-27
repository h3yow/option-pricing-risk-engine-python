"""情景冲击重估与风险汇总。

- 冲击生成新合约对象，不修改原合约（无跨请求共享可变状态）。
- 单笔失败隔离，记录可区分错误；汇总 total_pnl 仅累加成功腿，
  与逐笔重估结果严格一致。
- 可绑定行情版本（snapshot 已在入口处解析并校验存在）：同一请求所有
  合约共享同一版本，每条腿回带实际使用的 market_version。
"""
from __future__ import annotations

from .config import resolve_cross_check_tolerance
from .engine import PricingEngine
from .errors import ErrorCode, PricingError
from .market import resolve_market
from .models import (Contract, ItemError, MarketPrecedence, MarketSnapshot,
                     PricingMethod, ScenarioLegResult, ScenarioResponse,
                     ScenarioShockResult, Shock)


def apply_shock(c: Contract, shock: Shock) -> Contract:
    """返回冲击后的新合约（原对象不变）。"""
    return c.model_copy(update={
        "spot": c.spot * (1.0 + shock.spot_pct),
        "volatility": c.volatility + shock.vol_shift,
        "risk_free_rate": c.risk_free_rate + shock.rate_shift,
    })


def run_scenarios(engine: PricingEngine, name: str, contracts: list[Contract],
                  shocks: list[Shock], method: PricingMethod,
                  cross_check_tolerance: float | None = None,
                  snapshot: MarketSnapshot | None = None,
                  precedence: MarketPrecedence = MarketPrecedence.CONTRACT
                  ) -> ScenarioResponse:
    # 请求级容差在任何逐笔计算之前解析：非法取值整体拒绝
    tolerance = resolve_cross_check_tolerance(
        cross_check_tolerance, engine.settings.cross_check_tolerance)
    if len(shocks) > engine.settings.max_scenarios:
        raise PricingError(
            ErrorCode.LIMIT_EXCEEDED,
            f"情景数量 {len(shocks)} 超过上限 {engine.settings.max_scenarios}",
            {"count": len(shocks), "limit": engine.settings.max_scenarios})
    if len(contracts) > engine.settings.max_batch_size:
        raise PricingError(
            ErrorCode.LIMIT_EXCEEDED,
            f"合约数量 {len(contracts)} 超过上限 {engine.settings.max_batch_size}",
            {"count": len(contracts), "limit": engine.settings.max_batch_size})

    # 基准价格逐笔计算一次；失败腿在所有情景中保持失败。
    # 先逐笔解析行情（绑定版本时同一请求共享同一冻结快照）。
    resolved: dict[str, Contract] = {}
    versions: dict[str, str | None] = {}
    base: dict[str, float | None] = {}
    base_err: dict[str, ItemError] = {}
    for c in contracts:
        rc, version, mkt_err = resolve_market(c, snapshot, precedence)
        versions[c.contract_id] = version
        if mkt_err is not None:
            base[c.contract_id] = None
            base_err[c.contract_id] = mkt_err
            continue
        resolved[c.contract_id] = rc
        try:
            base[c.contract_id] = engine.price_one(
                rc, method, tolerance).price * c.quantity
        except PricingError as e:
            base[c.contract_id] = None
            base_err[c.contract_id] = ItemError(**e.to_dict())

    shock_results: list[ScenarioShockResult] = []
    for idx, shock in enumerate(shocks):
        legs: list[ScenarioLegResult] = []
        total_pnl = 0.0
        for c in contracts:
            cid = c.contract_id
            bp = base[cid]
            version = versions[cid]
            if bp is None:
                legs.append(ScenarioLegResult(contract_id=cid, ok=False,
                                              market_version=version,
                                              error=base_err[cid]))
                continue
            try:
                shocked = apply_shock(resolved[cid], shock)
                sp = engine.price_one(
                    shocked, method, tolerance).price * c.quantity
                legs.append(ScenarioLegResult(contract_id=cid, ok=True,
                                              base_price=bp, shocked_price=sp,
                                              pnl=sp - bp,
                                              market_version=version))
                total_pnl += sp - bp
            except PricingError as e:
                legs.append(ScenarioLegResult(contract_id=cid, ok=False,
                                              base_price=bp,
                                              market_version=version,
                                              error=ItemError(**e.to_dict())))
        succeeded = sum(1 for leg in legs if leg.ok)
        shock_results.append(ScenarioShockResult(
            shock_index=idx, total_pnl=total_pnl, legs=legs,
            succeeded=succeeded, failed=len(legs) - succeeded))
    market_version = snapshot.snapshot_id if snapshot is not None else None
    return ScenarioResponse(name=name, shocks=shock_results,
                            market_version=market_version)
