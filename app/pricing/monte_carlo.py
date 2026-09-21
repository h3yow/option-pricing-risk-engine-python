"""蒙特卡洛定价（途径二，独立实现）。

- 几何布朗运动风险中性模拟，期末一步精确离散化（无时间步误差）。
- 使用对偶变量（antithetic）降低方差。
- 每次调用构造独立 random.Random(seed)，不共享可变状态，结果可复现。
"""
from __future__ import annotations

import math
import random

from ..models import Contract, OptionType
from .black_scholes import validate_contract


def mc_price(c: Contract, paths: int, seed: int) -> float:
    validate_contract(c)
    if paths < 2:
        raise ValueError("paths 至少为 2")
    s, k, t, sig, r, q = (c.spot, c.strike, c.time_to_maturity,
                          c.volatility, c.risk_free_rate, c.dividend_yield)
    rng = random.Random(seed)
    drift = (r - q - 0.5 * sig * sig) * t
    diff = sig * math.sqrt(t)
    df = math.exp(-r * t)
    is_call = c.option_type is OptionType.CALL
    total = 0.0
    n = paths // 2
    for _ in range(n):
        z = rng.gauss(0.0, 1.0)
        for zz in (z, -z):
            st = s * math.exp(drift + diff * zz)
            payoff = max(st - k, 0.0) if is_call else max(k - st, 0.0)
            total += payoff
    return df * total / (2 * n)
