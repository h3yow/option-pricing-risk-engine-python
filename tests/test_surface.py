"""曲面查询：范围外拒绝、插值连续性与重复稳定性。"""

import pytest

from option_engine import PricingEngine, SurfaceOutOfRangeError, ValidationError
from option_engine.surface import Surface


@pytest.fixture
def surface():
    return Surface(
        (0.25, 0.5, 1.0, 2.0),
        (80.0, 100.0, 120.0),
        (
            (0.30, 0.25, 0.28),
            (0.28, 0.23, 0.26),
            (0.26, 0.21, 0.24),
            (0.24, 0.20, 0.23),
        ),
    )


class TestOutOfRange:
    @pytest.mark.parametrize(
        "maturity,strike",
        [
            (0.1, 100.0),   # 到期过短
            (2.5, 100.0),   # 到期过长
            (0.5, 79.9),    # 行权价过低
            (0.5, 120.1),   # 行权价过高
            (-0.5, 100.0),  # 负到期
        ],
    )
    def test_out_of_range_rejected(self, surface, maturity, strike):
        engine = PricingEngine()
        with pytest.raises(SurfaceOutOfRangeError) as exc_info:
            engine.query_vol_surface(surface, maturity, strike)
        assert exc_info.value.reason == "surface_out_of_range", (
            f"query ({maturity}, {strike}): got {exc_info.value.reason}"
        )

    def test_boundary_points_allowed(self, surface):
        assert surface.query(0.25, 80.0) == 0.30
        assert surface.query(2.0, 120.0) == 0.23


class TestInterpolation:
    def test_grid_points_exact(self, surface):
        assert surface.query(0.5, 100.0) == 0.23
        assert surface.query(1.0, 120.0) == 0.24

    def test_bilinear_midpoint(self, surface):
        v = surface.query(0.375, 90.0)  # 四格中点
        expected = (0.30 + 0.25 + 0.28 + 0.23) / 4.0
        assert abs(v - expected) < 1e-12, f"midpoint: got {v}, expected {expected}"

    def test_continuity(self, surface):
        # 查询点微小移动 => 结果微小变化（跨网格线也要连续）
        eps = 1e-6
        for m, k in [(0.5, 100.0), (0.75, 110.0), (1.0, 90.0)]:
            a = surface.query(m - eps, k)
            b = surface.query(m + eps, k)
            assert abs(a - b) < 1e-3, (
                f"discontinuity at maturity={m}: {a} vs {b}"
            )
            c = surface.query(m, k - eps)
            d = surface.query(m, k + eps)
            assert abs(c - d) < 1e-3, (
                f"discontinuity at strike={k}: {c} vs {d}"
            )

    def test_repeat_query_stable(self, surface):
        first = surface.query(0.7, 95.0)
        for _ in range(100):
            assert surface.query(0.7, 95.0) == first


class TestGridValidation:
    @pytest.mark.parametrize(
        "maturities,strikes,values",
        [
            ((1.0,), (90.0, 100.0), ((0.2, 0.2),)),               # 轴太短
            ((0.5, 0.5), (90.0, 100.0), ((0.2, 0.2), (0.2, 0.2))),  # 非严格递增
            ((0.5, 1.0), (90.0, 100.0), ((0.2, 0.2),)),            # 形状不符
            ((0.5, 1.0), (90.0, 100.0),
             ((0.2, float("nan")), (0.2, 0.2))),                   # 非有限值
        ],
        ids=["short_axis", "not_increasing", "shape_mismatch", "non_finite"],
    )
    def test_invalid_grid_rejected(self, maturities, strikes, values):
        with pytest.raises(ValidationError):
            Surface(maturities, strikes, values)
