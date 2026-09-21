"""情景冲击重估与风险汇总。

- 冲击生成新合约对象，不修改原合约（无跨请求共享可变状态）。
- 单笔失败隔离，记录可区分错误；汇总 total_pnl 仅累加成功腿，
  与逐笔重估结果严格一致。
"""
from __future__ import annotations

from .engine import PricingEngine
from .errors import ErrorCode, PricingError
from .models import (Contract, ItemError, PricingMethod, ScenarioLegResult,
                     ScenarioResponse, ScenarioShockResult, Shock)


def apply_shock(c: Contract, shock: Shock) -> Contract:
    """返回冲击后的新合约（原对象不变）。"""
    return c.model_copy(update={
        "spot": c.spot * (1.0 + shock.spot_pct),
        "volatility": c.volatility + shock.vol_shift,
        "risk_free_rate": c.risk_free_rate + shock.rate_shift,
    })


def run_scenarios(engine: PricingEngine, name: str, contracts: list[Contract],
                  shocks: list[Shock], method: PricingMethod) -> ScenarioResponse:
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

    # 基准价格逐笔计算一次；失败腿在所有情景中保持失败
    base: dict[str, float | None] = {}
    base_err: dict[str, ItemError] = {}
    for c in contracts:
        try:
            base[c.contract_id] = engine.price_one(c, method).price * c.quantity
        except PricingError as e:
            base[c.contract_id] = None
            base_err[c.contract_id] = ItemError(**e.to_dict())

    shock_results: list[ScenarioShockResult] = []
    for idx, shock in enumerate(shocks):
        legs: list[ScenarioLegResult] = []
        total_pnl = 0.0
        for c in contracts:
            bp = base[c.contract_id]
            if bp is None:
                legs.append(ScenarioLegResult(contract_id=c.contract_id, ok=False,
                                              error=base_err[c.contract_id]))
                continue
            try:
                shocked = apply_shock(c, shock)
                sp = engine.price_one(shocked, method).price * c.quantity
                legs.append(ScenarioLegResult(contract_id=c.contract_id, ok=True,
                                              base_price=bp, shocked_price=sp,
                                              pnl=sp - bp))
                total_pnl += sp - bp
            except PricingError as e:
                legs.append(ScenarioLegResult(contract_id=c.contract_id, ok=False,
                                              base_price=bp,
                                              error=ItemError(**e.to_dict())))
        succeeded = sum(1 for leg in legs if leg.ok)
        shock_results.append(ScenarioShockResult(
            shock_index=idx, total_pnl=total_pnl, legs=legs,
            succeeded=succeeded, failed=len(legs) - succeeded))
    return ScenarioResponse(name=name, shocks=shock_results)
