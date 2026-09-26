"""组合级情景重估：组合价值与关键敏感度汇总。

- 同一请求内所有合约共享同一份行情状态：进入时按优先级解析一次，
  冲击施加在解析后的取值上（行情版本在结果中可溯源）。
- 组合汇总（base_value / portfolio_value / total_pnl / greeks）仅累加
  成功腿，且按腿顺序逐项累加，与逐笔结果相加严格一致。
- 单笔失败隔离为腿级错误（含可区分错误码），不影响同一冲击下其余
  合约的计算与汇总。
- 合约数或冲击数超上限时在任何计算之前整体拒绝，不残留部分结果。
"""
from __future__ import annotations

from .engine import PricingEngine
from .errors import ErrorCode, PricingError
from .market import resolve_contract
from .models import (Contract, Greeks, ItemError, MarketPrecedence,
                     MarketSnapshot, PortfolioLegResult,
                     PortfolioScenarioResponse, PortfolioShockResult,
                     PricingMethod, Shock)
from .scenarios import apply_shock

_ZERO_GREEKS = Greeks(delta=0.0, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)


def _scale_greeks(g: Greeks, qty: float) -> Greeks:
    return Greeks(delta=g.delta * qty, gamma=g.gamma * qty, vega=g.vega * qty,
                  theta=g.theta * qty, rho=g.rho * qty)


def _add_greeks(a: Greeks, b: Greeks) -> Greeks:
    return Greeks(delta=a.delta + b.delta, gamma=a.gamma + b.gamma,
                  vega=a.vega + b.vega, theta=a.theta + b.theta,
                  rho=a.rho + b.rho)


def run_portfolio_scenarios(
    engine: PricingEngine,
    name: str,
    contracts: list[Contract],
    shocks: list[Shock],
    method: PricingMethod,
    market: MarketSnapshot | None = None,
    precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST,
    tolerance: float | None = None,
) -> PortfolioScenarioResponse:
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

    # 行情解析一次，全请求共享；之后以 CONTRACT_FIRST 重解析为恒等操作，
    # 保证冲击施加在解析后的取值上且结果可标记行情版本
    resolved = [resolve_contract(c, market, precedence) for c in contracts]
    mv = market.version if market is not None else None

    # 基准：逐笔定价一次（数量加权）；失败腿在所有冲击下保持失败
    base_price: dict[str, float | None] = {}
    base_greeks: dict[str, Greeks | None] = {}
    base_err: dict[str, ItemError] = {}
    for c in resolved:
        try:
            r = engine.price_one(c, method, tolerance, market,
                                 MarketPrecedence.CONTRACT_FIRST)
            base_price[c.contract_id] = r.price * c.quantity
            base_greeks[c.contract_id] = _scale_greeks(r.greeks, c.quantity)
        except PricingError as e:
            base_price[c.contract_id] = None
            base_greeks[c.contract_id] = None
            base_err[c.contract_id] = ItemError(**e.to_dict())

    # 组合基准汇总：按腿顺序逐项累加，与逐笔结果严格一致
    base_value = 0.0
    total_greeks = _ZERO_GREEKS
    for c in resolved:
        bp = base_price[c.contract_id]
        if bp is not None:
            base_value += bp
            total_greeks = _add_greeks(total_greeks, base_greeks[c.contract_id])

    shock_results: list[PortfolioShockResult] = []
    for idx, shock in enumerate(shocks):
        legs: list[PortfolioLegResult] = []
        portfolio_value = 0.0
        total_pnl = 0.0
        for c in resolved:
            bp = base_price[c.contract_id]
            if bp is None:
                legs.append(PortfolioLegResult(contract_id=c.contract_id, ok=False,
                                               market_version=mv,
                                               error=base_err[c.contract_id]))
                continue
            try:
                shocked = apply_shock(c, shock)
                r = engine.price_one(shocked, method, tolerance, market,
                                     MarketPrecedence.CONTRACT_FIRST)
                sp = r.price * c.quantity
                legs.append(PortfolioLegResult(
                    contract_id=c.contract_id, ok=True, base_price=bp,
                    shocked_price=sp, pnl=sp - bp,
                    greeks=base_greeks[c.contract_id], market_version=mv))
                portfolio_value += sp
                total_pnl += sp - bp
            except PricingError as e:
                legs.append(PortfolioLegResult(
                    contract_id=c.contract_id, ok=False, base_price=bp,
                    greeks=base_greeks[c.contract_id], market_version=mv,
                    error=ItemError(**e.to_dict())))
        succeeded = sum(1 for leg in legs if leg.ok)
        shock_results.append(PortfolioShockResult(
            shock_index=idx, portfolio_value=portfolio_value,
            total_pnl=total_pnl, legs=legs,
            succeeded=succeeded, failed=len(legs) - succeeded))
    return PortfolioScenarioResponse(name=name, base_value=base_value,
                                     greeks=total_greeks, market_version=mv,
                                     shocks=shock_results)
