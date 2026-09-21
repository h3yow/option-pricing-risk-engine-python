"""Black-Scholes 解析定价途径（含连续股息率）。"""

from __future__ import annotations

import math

from .models import Greeks, OptionContract, OptionType


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(c: OptionContract) -> tuple[float, float]:
    t = c.maturity
    sig = c.volatility
    if sig == 0.0:
        # 零波动率退化：d1/d2 趋于 ±inf，由内在价值符号决定
        moneyness = math.log(c.spot / c.strike) + (c.rate - c.dividend_yield) * t
        sign = 1.0 if moneyness > 0 else (-1.0 if moneyness < 0 else 0.0)
        return sign * math.inf, sign * math.inf
    sq = sig * math.sqrt(t)
    d1 = (math.log(c.spot / c.strike) + (c.rate - c.dividend_yield + 0.5 * sig * sig) * t) / sq
    return d1, d1 - sq


def _intrinsic(c: OptionContract) -> float:
    """到期 payoff 的贴现（零波动率极限）。"""
    fwd = c.spot * math.exp((c.rate - c.dividend_yield) * c.maturity)
    if c.option_type is OptionType.CALL:
        fwd_payoff = max(fwd - c.strike, 0.0)
    else:
        fwd_payoff = max(c.strike - fwd, 0.0)
    return fwd_payoff * math.exp(-c.rate * c.maturity)


def price(contract: OptionContract) -> float:
    c = contract
    if c.volatility == 0.0:
        return _intrinsic(c) * c.quantity
    d1, d2 = _d1_d2(c)
    disc_r = math.exp(-c.rate * c.maturity)
    disc_q = math.exp(-c.dividend_yield * c.maturity)
    if c.option_type is OptionType.CALL:
        p = c.spot * disc_q * _norm_cdf(d1) - c.strike * disc_r * _norm_cdf(d2)
    else:
        p = c.strike * disc_r * _norm_cdf(-d2) - c.spot * disc_q * _norm_cdf(-d1)
    return p * c.quantity


def greeks(contract: OptionContract) -> Greeks:
    c = contract
    if c.volatility == 0.0:
        # 退化情形：gamma/vega 为 0，delta 取贴现后的指示函数
        fwd = c.spot * math.exp((c.rate - c.dividend_yield) * c.maturity)
        disc_q = math.exp(-c.dividend_yield * c.maturity)
        if c.option_type is OptionType.CALL:
            delta = disc_q if fwd > c.strike else 0.0
        else:
            delta = -disc_q if fwd < c.strike else 0.0
        return Greeks(
            delta=delta * c.quantity,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
        )
    d1, d2 = _d1_d2(c)
    t = c.maturity
    sq = math.sqrt(t)
    disc_r = math.exp(-c.rate * t)
    disc_q = math.exp(-c.dividend_yield * t)
    pdf_d1 = _norm_pdf(d1)
    if c.option_type is OptionType.CALL:
        delta = disc_q * _norm_cdf(d1)
        rho = c.strike * t * disc_r * _norm_cdf(d2)
        theta = (
            -c.spot * disc_q * pdf_d1 * c.volatility / (2.0 * sq)
            - c.rate * c.strike * disc_r * _norm_cdf(d2)
            + c.dividend_yield * c.spot * disc_q * _norm_cdf(d1)
        )
    else:
        delta = -disc_q * _norm_cdf(-d1)
        rho = -c.strike * t * disc_r * _norm_cdf(-d2)
        theta = (
            -c.spot * disc_q * pdf_d1 * c.volatility / (2.0 * sq)
            + c.rate * c.strike * disc_r * _norm_cdf(-d2)
            - c.dividend_yield * c.spot * disc_q * _norm_cdf(-d1)
        )
    gamma = disc_q * pdf_d1 / (c.spot * c.volatility * sq)
    vega = c.spot * disc_q * pdf_d1 * sq
    q = c.quantity
    return Greeks(
        delta=delta * q,
        gamma=gamma * q,
        vega=vega * q,
        theta=theta * q,
        rho=rho * q,
    )
