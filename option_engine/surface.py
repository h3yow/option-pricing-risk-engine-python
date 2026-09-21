"""波动率/利率曲面：规则网格 + 双线性插值。

- 网格必须严格递增且至少 2 个点，否则构造时抛 ValidationError。
- 查询点超出网格范围抛 SurfaceOutOfRangeError，绝不静默外推。
- 插值在网格内部（含网格线）连续；同一查询重复执行结果稳定（无内部状态）。
"""

from __future__ import annotations

import bisect
import math

from .errors import SurfaceOutOfRangeError, ValidationError


class Surface:
    """按 (到期, 行权) 双线性插值的曲面。构造后不可变。"""

    def __init__(
        self,
        maturities: tuple[float, ...],
        strikes: tuple[float, ...],
        values: tuple[tuple[float, ...], ...],
    ) -> None:
        if len(maturities) < 2 or len(strikes) < 2:
            raise ValidationError(
                "surface grid needs at least 2 maturities and 2 strikes",
                reason="invalid_surface_grid",
            )
        for axis, name in ((maturities, "maturities"), (strikes, "strikes")):
            for v in axis:
                if not math.isfinite(v):
                    raise ValidationError(
                        f"surface axis {name} contains non-finite value",
                        reason="invalid_surface_grid",
                    )
            if any(b <= a for a, b in zip(axis, axis[1:])):
                raise ValidationError(
                    f"surface axis {name} must be strictly increasing",
                    reason="invalid_surface_grid",
                )
        if len(values) != len(maturities) or any(
            len(row) != len(strikes) for row in values
        ):
            raise ValidationError(
                "surface values shape does not match grid axes",
                reason="invalid_surface_grid",
            )
        for row in values:
            for v in row:
                if not math.isfinite(v):
                    raise ValidationError(
                        "surface values must be finite",
                        reason="invalid_surface_grid",
                    )
        self._maturities = tuple(maturities)
        self._strikes = tuple(strikes)
        self._values = tuple(tuple(row) for row in values)

    @property
    def maturities(self) -> tuple[float, ...]:
        return self._maturities

    @property
    def strikes(self) -> tuple[float, ...]:
        return self._strikes

    def _bracket(self, axis: tuple[float, ...], x: float, name: str) -> tuple[int, float]:
        if x < axis[0] or x > axis[-1]:
            raise SurfaceOutOfRangeError(
                f"{name}={x} outside surface range [{axis[0]}, {axis[-1]}]"
            )
        i = bisect.bisect_right(axis, x) - 1
        if i >= len(axis) - 1:
            return len(axis) - 2, 1.0
        span = axis[i + 1] - axis[i]
        return i, (x - axis[i]) / span

    def query(self, maturity: float, strike: float) -> float:
        if not (math.isfinite(maturity) and math.isfinite(strike)):
            raise ValidationError(
                f"query point not finite: ({maturity}, {strike})",
                reason="invalid_surface_query",
            )
        i, tm = self._bracket(self._maturities, maturity, "maturity")
        j, tk = self._bracket(self._strikes, strike, "strike")
        v00 = self._values[i][j]
        v01 = self._values[i][j + 1]
        v10 = self._values[i + 1][j]
        v11 = self._values[i + 1][j + 1]
        return (
            v00 * (1 - tm) * (1 - tk)
            + v01 * (1 - tm) * tk
            + v10 * tm * (1 - tk)
            + v11 * tm * tk
        )
