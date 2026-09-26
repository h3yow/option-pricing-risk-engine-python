"""蒙特卡洛定价（途径二，独立实现）。

- 几何布朗运动风险中性模拟，期末一步精确离散化（无时间步误差）。
- 分层抽样（stratified sampling）缩减方差：把标准正态划分 N 个等概率层，
  每层用统一随机偏移 u=(j+U)/N 取一个代表样本；在深度实值/深度虚值、
  短到期等尾部事件主导的参数范围内，标准误远小于普通/对偶变量抽样，
  使默认交叉验证容差下正常合约不会被误判为分歧。
- 每次调用构造独立 random.Random(seed)，仅从其中取一个 U，不共享可变
  状态，结果可复现。
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist

from ..models import Contract, OptionType
from .black_scholes import validate_contract

_NORM_INV_CDF = NormalDist().inv_cdf


def mc_price(c: Contract, paths: int, seed: int) -> float:
    validate_contract(c)
    if paths < 1:
        raise ValueError("paths 至少为 1")
    s, k, t, sig, r, q = (c.spot, c.strike, c.time_to_maturity,
                          c.volatility, c.risk_free_rate, c.dividend_yield)
    rng = random.Random(seed)
    shift = rng.random()
    drift = (r - q - 0.5 * sig * sig) * t
    diff = sig * math.sqrt(t)
    df = math.exp(-r * t)
    is_call = c.option_type is OptionType.CALL
    total = 0.0
    for j in range(paths):
        u = (j + shift) / paths
        st = s * math.exp(drift + diff * _NORM_INV_CDF(u))
        payoff = max(st - k, 0.0) if is_call else max(k - st, 0.0)
        total += payoff
    return df * total / paths
