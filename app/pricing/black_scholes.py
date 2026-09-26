"""Black-Scholes 闭式解定价（途径一）。

约定：连续复利；dividend_yield 为连续股息率；时间以年计。
"""
from __future__ import annotations

import math

from ..errors import ErrorCode, PricingError
from ..models import Contract, Greeks, OptionType


def _check_finite(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise PricingError(ErrorCode.VALIDATION, f"字段 {name} 缺失或非有限数值: {value!r}",
                           {"field": name, "value": repr(value)})


def validate_contract(c: Contract) -> None:
    """校验合约数值字段；非法输入抛出 PricingError(VALIDATION)，不用默认值兜底。"""
    for name in ("spot", "strike", "time_to_maturity", "volatility",
                 "risk_free_rate", "dividend_yield", "quantity"):
        _check_finite(name, getattr(c, name))
    if c.time_to_maturity <= 0:
        raise PricingError(ErrorCode.VALIDATION,
                           f"到期时间必须为正: {c.time_to_maturity}",
                           {"field": "time_to_maturity", "value": c.time_to_maturity})
    if c.volatility < 0:
        raise PricingError(ErrorCode.VALIDATION,
                           f"波动率不得为负: {c.volatility}",
                           {"field": "volatility", "value": c.volatility})
    if c.spot <= 0:
        raise PricingError(ErrorCode.VALIDATION, f"标的价格必须为正: {c.spot}",
                           {"field": "spot", "value": c.spot})
    if c.strike <= 0:
        raise PricingError(ErrorCode.VALIDATION, f"行权价必须为正: {c.strike}",
                           {"field": "strike", "value": c.strike})


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(c: Contract) -> tuple[float, float]:
    s, k, t, sig, r, q = (c.spot, c.strike, c.time_to_maturity,
                          c.volatility, c.risk_free_rate, c.dividend_yield)
    if sig == 0.0:
        # 零波动率退化：d 趋于 ±inf，由内在价值符号决定
        moneyness = math.log(s / k) + (r - q) * t
        sign = 1.0 if moneyness > 0 else (-1.0 if moneyness < 0 else 0.0)
        inf = math.inf if sign >= 0 else -math.inf
        return inf, inf
    d1 = (math.log(s / k) + (r - q + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
    return d1, d1 - sig * math.sqrt(t)


def bs_price(c: Contract) -> float:
    validate_contract(c)
    s, k, t, sig, r, q = (c.spot, c.strike, c.time_to_maturity,
                          c.volatility, c.risk_free_rate, c.dividend_yield)
    d1, d2 = _d1_d2(c)
    df_r, df_q = math.exp(-r * t), math.exp(-q * t)
    if c.option_type is OptionType.CALL:
        price = s * df_q * _norm_cdf(d1) - k * df_r * _norm_cdf(d2)
    else:
        price = k * df_r * _norm_cdf(-d2) - s * df_q * _norm_cdf(-d1)
    # 深度虚值时两项接近相等，浮点相消可能给出约 -1e-16；期权价值以 0
    # 为下界，钳为零避免把正常合约判成异常
    return max(price, 0.0)


def bs_greeks(c: Contract) -> Greeks:
    validate_contract(c)
    s, k, t, sig, r, q = (c.spot, c.strike, c.time_to_maturity,
                          c.volatility, c.risk_free_rate, c.dividend_yield)
    d1, d2 = _d1_d2(c)
    df_r, df_q = math.exp(-r * t), math.exp(-q * t)
    pdf = _norm_pdf(d1)
    sqrt_t = math.sqrt(t)
    gamma = df_q * pdf / (s * sig * sqrt_t) if sig > 0 else 0.0
    vega = s * df_q * pdf * sqrt_t / 100.0  # 每 1 个波动率百分点
    if c.option_type is OptionType.CALL:
        delta = df_q * _norm_cdf(d1)
        theta = (-(s * df_q * pdf * sig) / (2 * sqrt_t)
                 - r * k * df_r * _norm_cdf(d2) + q * s * df_q * _norm_cdf(d1)) / 365.0
        rho = k * t * df_r * _norm_cdf(d2) / 100.0
    else:
        delta = df_q * (_norm_cdf(d1) - 1.0)
        theta = (-(s * df_q * pdf * sig) / (2 * sqrt_t)
                 + r * k * df_r * _norm_cdf(-d2) - q * s * df_q * _norm_cdf(-d1)) / 365.0
        rho = -k * t * df_r * _norm_cdf(-d2) / 100.0
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
