"""曲面：范围外拒绝、插值连续、重复查询稳定、不可变。"""
import pytest

from app.errors import ErrorCode, PricingError
from app.surface import Surface


@pytest.fixture()
def surface():
    return Surface(maturities=[0.5, 1.0, 2.0],
                   strikes=[90.0, 100.0, 110.0],
                   values=[[0.25, 0.20, 0.22],
                           [0.28, 0.23, 0.24],
                           [0.30, 0.26, 0.27]],
                   name="vol")


def test_node_values_exact(surface):
    assert surface.query(1.0, 100.0) == 0.23


def test_bilinear_midpoint(surface):
    got = surface.query(0.75, 95.0)
    expected = (0.25 + 0.20 + 0.28 + 0.23) / 4
    assert abs(got - expected) < 1e-15, f"中点双线性插值应为 {expected}，实际 {got}"


def test_interpolation_continuous(surface):
    # 查询点微小移动 → 结果微小变化（无跳变）
    v1 = surface.query(1.0, 100.0)
    v2 = surface.query(1.0, 100.0 + 1e-6)
    assert abs(v2 - v1) < 1e-4, f"连续性破坏: {v1} vs {v2}"


def test_repeat_query_stable(surface):
    assert surface.query(1.3, 97.5) == surface.query(1.3, 97.5)


@pytest.mark.parametrize("t,k", [(0.49, 100.0), (2.01, 100.0), (1.0, 89.9),
                                 (1.0, 110.1), (-1.0, 100.0)])
def test_out_of_range_rejected_not_extrapolated(surface, t, k):
    with pytest.raises(PricingError) as exc_info:
        surface.query(t, k)
    err = exc_info.value
    assert err.code is ErrorCode.OUT_OF_SURFACE, (
        f"查询点 (T={t}, K={k}) 在曲面外，应报 OUT_OF_SURFACE，实际 {err.code}")
    assert "不外推" in err.message


def test_invalid_grid_rejected():
    with pytest.raises(PricingError, match="严格递增"):
        Surface([1.0, 0.5], [90.0, 100.0], [[0.2, 0.2], [0.2, 0.2]])
    with pytest.raises(PricingError, match="维度"):
        Surface([0.5, 1.0], [90.0, 100.0], [[0.2, 0.2]])
    with pytest.raises(PricingError, match="非有限"):
        Surface([0.5, 1.0], [90.0, 100.0], [[0.2, float("nan")], [0.2, 0.2]])


def test_surface_immutable_after_external_mutation():
    mats = [0.5, 1.0]
    vals = [[0.2, 0.2], [0.3, 0.3]]
    s = Surface(mats, [90.0, 100.0], vals)
    mats[0] = -999.0
    vals[0][0] = -999.0
    assert s.query(0.5, 90.0) == 0.2, "外部修改不应影响已构造曲面"
