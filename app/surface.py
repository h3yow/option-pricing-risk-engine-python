"""波动率/利率曲面：规则网格双线性插值。

- 构造时校验网格单调递增、数值有限，之后对象不可变，可并发安全查询。
- 查询点超出网格范围一律拒绝（OUT_OF_SURFACE），绝不静默外推。
- 双线性插值保证结果随查询点连续变化；纯函数实现保证重复查询结果稳定。
"""
from __future__ import annotations

import bisect
import math

from .errors import ErrorCode, PricingError


class Surface:
    def __init__(self, maturities: list[float], strikes: list[float],
                 values: list[list[float]], name: str = "surface"):
        if len(maturities) < 2 or len(strikes) < 2:
            raise PricingError(ErrorCode.VALIDATION,
                               f"曲面 {name} 每个维度至少需要 2 个网格点")
        for axis, label in ((maturities, "maturities"), (strikes, "strikes")):
            for v in axis:
                if not math.isfinite(v):
                    raise PricingError(ErrorCode.VALIDATION,
                                       f"曲面 {name} 轴 {label} 含非有限值: {v!r}")
            if any(b <= a for a, b in zip(axis, axis[1:])):
                raise PricingError(ErrorCode.VALIDATION,
                                   f"曲面 {name} 轴 {label} 必须严格递增")
        if len(values) != len(maturities) or any(len(row) != len(strikes) for row in values):
            raise PricingError(ErrorCode.VALIDATION,
                               f"曲面 {name} 数值网格维度与轴不匹配")
        for row in values:
            for v in row:
                if not math.isfinite(v):
                    raise PricingError(ErrorCode.VALIDATION,
                                       f"曲面 {name} 含非有限值: {v!r}")
        # 拷贝为不可变结构，防止外部修改导致半更新状态
        self._maturities = tuple(maturities)
        self._strikes = tuple(strikes)
        self._values = tuple(tuple(row) for row in values)
        self.name = name

    @property
    def maturity_range(self) -> tuple[float, float]:
        return self._maturities[0], self._maturities[-1]

    @property
    def strike_range(self) -> tuple[float, float]:
        return self._strikes[0], self._strikes[-1]

    def query(self, maturity: float, strike: float) -> float:
        if not (math.isfinite(maturity) and math.isfinite(strike)):
            raise PricingError(ErrorCode.VALIDATION,
                               f"查询点非有限: maturity={maturity!r}, strike={strike!r}")
        m0, m1 = self.maturity_range
        k0, k1 = self.strike_range
        if not (m0 <= maturity <= m1) or not (k0 <= strike <= k1):
            raise PricingError(
                ErrorCode.OUT_OF_SURFACE,
                f"查询点 (T={maturity}, K={strike}) 超出曲面 {self.name} "
                f"范围 T∈[{m0},{m1}], K∈[{k0},{k1}]，不外推",
                {"maturity": maturity, "strike": strike,
                 "maturity_range": [m0, m1], "strike_range": [k0, k1]})
        i = bisect.bisect_right(self._maturities, maturity) - 1
        j = bisect.bisect_right(self._strikes, strike) - 1
        i = min(i, len(self._maturities) - 2)
        j = min(j, len(self._strikes) - 2)
        t0, t1 = self._maturities[i], self._maturities[i + 1]
        k0_, k1_ = self._strikes[j], self._strikes[j + 1]
        wt = 0.0 if t1 == t0 else (maturity - t0) / (t1 - t0)
        wk = 0.0 if k1_ == k0_ else (strike - k0_) / (k1_ - k0_)
        v00 = self._values[i][j]
        v01 = self._values[i][j + 1]
        v10 = self._values[i + 1][j]
        v11 = self._values[i + 1][j + 1]
        return ((1 - wt) * (1 - wk) * v00 + (1 - wt) * wk * v01
                + wt * (1 - wk) * v10 + wt * wk * v11)
