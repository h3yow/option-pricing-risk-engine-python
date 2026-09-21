"""CRR 二叉树定价途径（独立于 Black-Scholes 解析解）。

delta/gamma/theta 直接由树节点计算（数值稳定），
vega/rho 通过对树价格做中心差分得到，均不依赖解析公式。
"""

from __future__ import annotations

import dataclasses
import math

from .models import Greeks, OptionContract, OptionType


class _Tree:
    """一次 CRR 树的完整回溯结果，保留前两步节点用于希腊字母。"""

    def __init__(self, c: OptionContract, steps: int) -> None:
        t = c.maturity
        sig = c.volatility
        dt = t / steps
        self.dt = dt
        if sig == 0.0:
            s_t = c.spot * math.exp((c.rate - c.dividend_yield) * t)
            if c.option_type is OptionType.CALL:
                payoff = max(s_t - c.strike, 0.0)
            else:
                payoff = max(c.strike - s_t, 0.0)
            v = payoff * math.exp(-c.rate * t)
            self.price = v
            self.nodes1 = None
            self.nodes2 = None
            return
        u = math.exp(sig * math.sqrt(dt))
        d = 1.0 / u
        growth = math.exp((c.rate - c.dividend_yield) * dt)
        p = (growth - d) / (u - d)
        disc = math.exp(-c.rate * dt)
        values = []
        for j in range(steps + 1):
            s = c.spot * (u ** j) * (d ** (steps - j))
            if c.option_type is OptionType.CALL:
                values.append(max(s - c.strike, 0.0))
            else:
                values.append(max(c.strike - s, 0.0))
        for i in range(steps):
            values = [
                disc * (p * values[j + 1] + (1.0 - p) * values[j])
                for j in range(len(values) - 1)
            ]
            if i == steps - 3:
                # 深度 2 节点（自底向上第 2 层）：(spot, value)
                self.nodes2 = tuple(
                    (c.spot * (u ** j) * (d ** (2 - j)), values[j]) for j in range(3)
                )
            elif i == steps - 2:
                self.nodes1 = tuple(
                    (c.spot * (u ** j) * (d ** (1 - j)), values[j]) for j in range(2)
                )
        self.price = values[0]

    def delta(self) -> float:
        if self.nodes1 is None:
            return 0.0
        (s_d, v_d), (s_u, v_u) = self.nodes1
        return (v_u - v_d) / (s_u - s_d)

    def gamma(self) -> float:
        if self.nodes2 is None:
            return 0.0
        (s_dd, v_dd), (s_ud, v_ud), (s_uu, v_uu) = self.nodes2
        d_up = (v_uu - v_ud) / (s_uu - s_ud)
        d_dn = (v_ud - v_dd) / (s_ud - s_dd)
        return (d_up - d_dn) / ((s_uu - s_dd) / 2.0)

    def theta(self) -> float:
        if self.nodes2 is None:
            return 0.0
        v_mid = self.nodes2[1][1]
        return (v_mid - self.price) / (2.0 * self.dt)


def price(contract: OptionContract, steps: int = 200) -> float:
    if steps < 2:
        raise ValueError("steps must be >= 2")
    return _Tree(contract, steps).price * contract.quantity


def greeks(contract: OptionContract, steps: int = 200) -> Greeks:
    if steps < 2:
        raise ValueError("steps must be >= 2")
    c = contract
    unit = dataclasses.replace(c, quantity=1.0)
    tree = _Tree(unit, steps)

    h_v = 1e-3
    vega = (
        _Tree(dataclasses.replace(unit, volatility=c.volatility + h_v), steps).price
        - _Tree(dataclasses.replace(unit, volatility=max(c.volatility - h_v, 0.0)), steps).price
    ) / (2.0 * h_v)

    h_r = 1e-4
    rho = (
        _Tree(dataclasses.replace(unit, rate=c.rate + h_r), steps).price
        - _Tree(dataclasses.replace(unit, rate=c.rate - h_r), steps).price
    ) / (2.0 * h_r)

    q = c.quantity
    return Greeks(
        delta=tree.delta() * q,
        gamma=tree.gamma() * q,
        vega=vega * q,
        theta=tree.theta() * q,
        rho=rho * q,
    )
