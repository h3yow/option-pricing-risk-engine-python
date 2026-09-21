"""由价格反解隐含波动率。

流程：输入校验 → 无套利边界检查 → 二分迭代。
失败均抛出可区分 PricingError，绝不返回负数或未收敛结果。
"""
from __future__ import annotations

import math

from .errors import ErrorCode, PricingError
from .models import OptionType
from .pricing.black_scholes import bs_price, _check_finite

# 波动率搜索上界（1000%），足以覆盖实际行情
_VOL_LOW = 1e-9
_VOL_HIGH = 10.0


def no_arbitrage_bounds(option_type: OptionType, spot: float, strike: float,
                        t: float, r: float, q: float) -> tuple[float, float]:
    """欧式期权无套利价格区间 (下界, 上界)，连续复利折现。"""
    df_r, df_q = math.exp(-r * t), math.exp(-q * t)
    fwd_disc = spot * df_q
    strike_disc = strike * df_r
    if option_type is OptionType.CALL:
        return max(fwd_disc - strike_disc, 0.0), fwd_disc
    return max(strike_disc - fwd_disc, 0.0), strike_disc


def _validate_inputs(spot: float, strike: float, t: float, r: float, q: float,
                     target_price: float) -> None:
    for name, v in (("spot", spot), ("strike", strike), ("time_to_maturity", t),
                    ("risk_free_rate", r), ("dividend_yield", q),
                    ("target_price", target_price)):
        _check_finite(name, v)
    if t <= 0:
        raise PricingError(ErrorCode.VALIDATION, f"到期时间必须为正: {t}",
                           {"field": "time_to_maturity", "value": t})
    if spot <= 0:
        raise PricingError(ErrorCode.VALIDATION, f"标的价格必须为正: {spot}",
                           {"field": "spot", "value": spot})
    if strike <= 0:
        raise PricingError(ErrorCode.VALIDATION, f"行权价必须为正: {strike}",
                           {"field": "strike", "value": strike})
    if target_price < 0:
        raise PricingError(ErrorCode.VALIDATION, f"目标价格不得为负: {target_price}",
                           {"field": "target_price", "value": target_price})


def implied_vol(option_type: OptionType, spot: float, strike: float, t: float,
                r: float, q: float, target_price: float,
                max_iterations: int, tolerance: float) -> tuple[float, int, float]:
    """返回 (隐含波动率, 迭代次数, 残差)。"""
    _validate_inputs(spot, strike, t, r, q, target_price)

    lower, upper = no_arbitrage_bounds(option_type, spot, strike, t, r, q)
    eps = 1e-12 * max(1.0, upper)
    if target_price < lower - eps or target_price > upper + eps:
        raise PricingError(
            ErrorCode.NO_ARBITRAGE_BOUNDS,
            f"目标价格 {target_price} 超出无套利区间 [{lower}, {upper}]",
            {"target_price": target_price, "lower": lower, "upper": upper})

    from .models import Contract  # 局部导入避免循环
    def price_at(vol: float) -> float:
        c = Contract(contract_id="__iv__", option_type=option_type, spot=spot,
                     strike=strike, time_to_maturity=t, volatility=vol,
                     risk_free_rate=r, dividend_yield=q)
        return bs_price(c)

    # 边界情形：目标价即下界 → 隐含波动率为 0（深度边界，可接受）
    if abs(target_price - lower) <= tolerance:
        return 0.0, 0, 0.0

    hi_price = price_at(_VOL_HIGH)
    if target_price > hi_price + tolerance:
        raise PricingError(
            ErrorCode.NO_SOLUTION,
            f"目标价格 {target_price} 高于波动率上界 {_VOL_HIGH} 对应价格 {hi_price}，无解",
            {"target_price": target_price, "max_price": hi_price, "vol_high": _VOL_HIGH})

    lo, hi = _VOL_LOW, _VOL_HIGH
    best_vol, best_residual = 0.0, math.inf
    for iteration in range(1, max_iterations + 1):
        mid = 0.5 * (lo + hi)
        price = price_at(mid)
        residual = price - target_price
        if abs(residual) < abs(best_residual):
            best_vol, best_residual = mid, residual
        if abs(residual) <= tolerance:
            return mid, iteration, residual
        if residual > 0:
            hi = mid
        else:
            lo = mid
    raise PricingError(
        ErrorCode.NOT_CONVERGED,
        f"二分 {max_iterations} 次未收敛，最优残差 {best_residual}",
        {"iterations": max_iterations, "best_vol": best_vol,
         "residual": best_residual, "tolerance": tolerance})
