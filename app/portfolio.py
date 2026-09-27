"""组合级情景重估与行情版本绑定。

约定：
- 一次请求内所有合约共享同一行情版本；按 precedence 决定合约级取值与
  行情版本的优先级；每条腿结果标注实际使用的行情版本。
- 组合汇总（基准价值/冲击后价值/PnL/希腊字母）在构造时由同一份成功腿
  列表直接求和得到，与逐笔结果严格一致（不是另一套算法）。
- 单笔失败（基准非法、冲击后非法、行情字段缺失）隔离为腿级错误，不影响
  同一冲击下其余合约，也不进入汇总。
- 组合规模或冲击数量超过配置上限、或引用不存在的行情版本：在任何计算
  之前整体拒绝，不残留部分结果。
"""
from __future__ import annotations

from .config import resolve_cross_check_tolerance
from .engine import PricingEngine
from .errors import ErrorCode, PricingError
from .market import resolve_market
from .market_registry import MarketRegistry
from .models import (Contract, ItemError, PortfolioGreeks, PortfolioLegResult,
                     PortfolioScenarioRequest, PortfolioScenarioResponse,
                     PortfolioShockResult, PriceResult)
from .scenarios import apply_shock

# 向后兼容：历史调用方从 app.portfolio 导入 resolve_market
__all__ = ["resolve_market", "run_portfolio_scenarios"]


def _scale_greeks(result: PriceResult, quantity: float) -> PortfolioGreeks:
    g = result.greeks
    return PortfolioGreeks(
        delta=g.delta * quantity, gamma=g.gamma * quantity,
        vega=g.vega * quantity, theta=g.theta * quantity,
        rho=g.rho * quantity)


def run_portfolio_scenarios(engine: PricingEngine,
                            registry: MarketRegistry,
                            req: PortfolioScenarioRequest
                            ) -> PortfolioScenarioResponse:
    # 0) 请求级容差在任何计算之前解析：非法取值整体拒绝，不残留部分结果
    tolerance = resolve_cross_check_tolerance(
        req.cross_check_tolerance, engine.settings.cross_check_tolerance)

    # 1) 上限校验：整体拒绝，发生在任何计算之前
    if len(req.contracts) > engine.settings.max_batch_size:
        raise PricingError(
            ErrorCode.LIMIT_EXCEEDED,
            f"组合合约数量 {len(req.contracts)} 超过上限 "
            f"{engine.settings.max_batch_size}",
            {"size": len(req.contracts),
             "limit": engine.settings.max_batch_size})
    if len(req.shocks) > engine.settings.max_scenarios:
        raise PricingError(
            ErrorCode.LIMIT_EXCEEDED,
            f"情景数量 {len(req.shocks)} 超过上限 "
            f"{engine.settings.max_scenarios}",
            {"size": len(req.shocks),
             "limit": engine.settings.max_scenarios})

    # 2) 行情版本绑定（请求级）：版本不存在整体拒绝
    snapshot = None if req.market_version is None else registry.get(
        req.market_version)

    # 3) 逐笔解析行情 + 基准重估（一次）
    base_value: dict[str, float | None] = {}
    base_version: dict[str, str | None] = {}
    base_err: dict[str, ItemError] = {}
    resolved_contracts: dict[str, Contract] = {}
    for c in req.contracts:
        resolved, version, mkt_err = resolve_market(c, snapshot, req.precedence)
        base_version[c.contract_id] = version
        if mkt_err is not None:
            base_value[c.contract_id] = None
            base_err[c.contract_id] = mkt_err
            continue
        try:
            r = engine.price_one(resolved, req.method, tolerance)
            base_value[c.contract_id] = r.price * c.quantity
            resolved_contracts[c.contract_id] = resolved
        except PricingError as e:
            base_value[c.contract_id] = None
            base_err[c.contract_id] = ItemError(**e.to_dict())

    base_total = sum(v for v in base_value.values() if v is not None)
    base_succeeded = sum(1 for v in base_value.values() if v is not None)

    # 4) 逐冲击重估；汇总由同一份成功腿直接求和
    shock_results: list[PortfolioShockResult] = []
    for idx, shock in enumerate(req.shocks):
        legs: list[PortfolioLegResult] = []
        for c in req.contracts:
            cid = c.contract_id
            bv = base_value[cid]
            version = base_version[cid]
            if bv is None:
                legs.append(PortfolioLegResult(
                    contract_id=cid, shock_index=idx, ok=False,
                    market_version=version, error=base_err[cid]))
                continue
            try:
                shocked = apply_shock(resolved_contracts[cid], shock)
                r = engine.price_one(shocked, req.method, tolerance)
                sv = r.price * c.quantity
                legs.append(PortfolioLegResult(
                    contract_id=cid, shock_index=idx, ok=True,
                    base_price=bv, shocked_price=sv, pnl=sv - bv,
                    greeks=_scale_greeks(r, c.quantity),
                    market_version=version))
            except PricingError as e:
                legs.append(PortfolioLegResult(
                    contract_id=cid, shock_index=idx, ok=False,
                    base_price=bv, market_version=version,
                    error=ItemError(**e.to_dict())))

        good = [leg for leg in legs if leg.ok]
        greeks = PortfolioGreeks(
            delta=sum(l.greeks.delta for l in good),
            gamma=sum(l.greeks.gamma for l in good),
            vega=sum(l.greeks.vega for l in good),
            theta=sum(l.greeks.theta for l in good),
            rho=sum(l.greeks.rho for l in good))
        shock_results.append(PortfolioShockResult(
            shock_index=idx,
            total_base_value=sum(l.base_price for l in good),
            total_value=sum(l.shocked_price for l in good),
            total_pnl=sum(l.pnl for l in good),
            greeks=greeks, legs=legs,
            succeeded=len(good), failed=len(legs) - len(good)))

    return PortfolioScenarioResponse(
        name=req.name, market_version=req.market_version,
        precedence=req.precedence.value, base_total_value=base_total,
        shocks=shock_results, succeeded=base_succeeded,
        failed=len(req.contracts) - base_succeeded)
