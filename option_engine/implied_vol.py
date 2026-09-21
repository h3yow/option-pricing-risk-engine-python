"""由价格反解隐含波动率（二分法 + 无套利边界预检）。

失败情形均抛 ImpliedVolError，并以可区分的 reason 说明：
- "price_below_no_arbitrage_bound": 目标价低于下界，无解
- "price_above_no_arbitrage_bound": 目标价高于上界，无解
- "not_converged": 迭代次数用尽仍未收敛
"""

from __future__ import annotations

import dataclasses
import math

from . import black_scholes
from .errors import ImpliedVolError
from .models import OptionContract, OptionType

_BOUND_TOL = 1e-10


def no_arbitrage_bounds(contract: OptionContract) -> tuple[float, float]:
    """返回单位数量合约价格的无套利 (下界, 上界)。"""
    c = contract
    disc_r = math.exp(-c.rate * c.maturity)
    disc_q = math.exp(-c.dividend_yield * c.maturity)
    if c.option_type is OptionType.CALL:
        lo = max(c.spot * disc_q - c.strike * disc_r, 0.0)
        hi = c.spot * disc_q
    else:
        lo = max(c.strike * disc_r - c.spot * disc_q, 0.0)
        hi = c.strike * disc_r
    return lo, hi


def implied_volatility(
    contract: OptionContract,
    target_price: float,
    *,
    max_iterations: int = 200,
    tolerance: float = 1e-8,
    max_vol: float = 5.0,
) -> float:
    """反解隐含波动率；失败抛 ImpliedVolError，绝不返回未收敛结果。"""
    if not math.isfinite(target_price):
        raise ImpliedVolError(
            f"target price is not finite: {target_price}", reason="invalid_target_price"
        )
    unit = dataclasses.replace(contract, quantity=1.0)
    target = target_price / contract.quantity  # 归一到单位数量
    lo_bound, hi_bound = no_arbitrage_bounds(unit)
    if target < lo_bound - _BOUND_TOL:
        raise ImpliedVolError(
            f"target price {target} below no-arbitrage lower bound {lo_bound}",
            reason="price_below_no_arbitrage_bound",
        )
    if target > hi_bound + _BOUND_TOL:
        raise ImpliedVolError(
            f"target price {target} above no-arbitrage upper bound {hi_bound}",
            reason="price_above_no_arbitrage_bound",
        )

    # 边界退化：价格等于下界 => 零波动率
    if target <= lo_bound + _BOUND_TOL:
        return 0.0

    lo, hi = 0.0, max_vol
    price_hi = black_scholes.price(dataclasses.replace(unit, volatility=hi))
    if price_hi < target:
        raise ImpliedVolError(
            f"no solution: price at max vol {max_vol} is {price_hi} < target {target}",
            reason="price_above_no_arbitrage_bound",
        )

    for _ in range(max_iterations):
        mid = 0.5 * (lo + hi)
        price_mid = black_scholes.price(dataclasses.replace(unit, volatility=mid))
        if abs(price_mid - target) <= tolerance:
            return mid
        if price_mid < target:
            lo = mid
        else:
            hi = mid
    raise ImpliedVolError(
        f"implied vol did not converge in {max_iterations} iterations "
        f"(bracket [{lo}, {hi}], target {target})",
        reason="not_converged",
    )
