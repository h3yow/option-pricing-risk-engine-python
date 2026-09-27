"""请求级双途径一致性容差：默认/收紧两条路径、批量与情景生效、非法取值拒绝。"""
import pytest

from app.config import load_settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.models import (BatchPriceRequest, MarketPrecedence, PortfolioScenarioRequest,
                        PriceRequest, PricingMethod, ScenarioRequest, Shock)
from app.portfolio import run_portfolio_scenarios
from app.scenarios import run_scenarios
from tests.conftest import make_contract


@pytest.fixture()
def engine():
    return PricingEngine(load_settings({}))


@pytest.mark.parametrize("bad", ["0.01", True, 0, -0.02, 0.0, float("nan"),
                                 float("inf"), float("-inf"), [0.01], object()])
def test_single_price_invalid_tolerance_rejected_at_request_level(bad):
    # 在 pydantic 请求模型层即拒绝，且错误可区分（VALIDATION），不当作没传
    with pytest.raises(PricingError) as exc:
        PriceRequest(contract=make_contract(),
                     cross_check_tolerance=bad)
    err = exc.value
    assert err.code is ErrorCode.VALIDATION, (
        f"非法容差 {bad!r} 应报 VALIDATION，实际 {getattr(err, 'code', None)}")
    assert err.detail.get("field") == "cross_check_tolerance"


def test_single_price_default_tolerance_matches_engine_default(engine):
    # 不传：与既有默认行为完全一致
    c = make_contract()
    req = PriceRequest(contract=c)
    assert req.cross_check_tolerance is None
    r = engine.price_one(c, PricingMethod.CROSS_CHECK, req.cross_check_tolerance)
    assert r.method == "cross_check"


def test_single_price_tight_tolerance_diverges_with_full_evidence(engine):
    # 同一合约：默认通过；显式收紧到 1e-9 必须如实报分歧并给出双方数值/容差/依据
    c = make_contract()
    engine.price_one(c, PricingMethod.CROSS_CHECK)  # 默认不报分歧

    with pytest.raises(PricingError) as exc:
        engine.price_one(c, PricingMethod.CROSS_CHECK, 1e-9)
    err = exc.value
    assert err.code is ErrorCode.METHOD_DIVERGENCE
    d = err.detail
    assert {"analytic", "monte_carlo", "rel_diff", "tolerance",
            "criterion"} <= set(d)
    assert d["tolerance"] == 1e-9
    assert d["criterion"] == "rel_diff > tolerance"
    assert d["rel_diff"] == pytest.approx(
        abs(d["analytic"] - d["monte_carlo"]) / max(abs(d["analytic"]), 1.0))
    assert d["rel_diff"] > 1e-9


def test_relaxed_tolerance_still_passes_and_uses_request_value(engine):
    # 显式给一个很宽的正值：实际所用容差就是请求值（此处仅校验不报错）
    c = make_contract()
    r = engine.price_one(c, PricingMethod.CROSS_CHECK, 0.5)
    assert r.method == "cross_check"


def test_batch_request_tolerance_actually_applies_per_item(engine):
    # 批量：收紧容差后每笔都如实给出分歧（逐笔隔离，带双方数值/容差/依据）
    contracts = [make_contract(contract_id="a"),
                 make_contract(contract_id="b", strike=105.0)]
    req = BatchPriceRequest(contracts=contracts,
                            method=PricingMethod.CROSS_CHECK,
                            cross_check_tolerance=1e-9)
    resp = engine.price_batch(contracts, req.method, req.cross_check_tolerance)
    assert resp.succeeded == 0 and resp.failed == 2
    for item in resp.results:
        assert not item.ok and item.error.code == "METHOD_DIVERGENCE"
        d = item.error.detail
        assert d["tolerance"] == 1e-9
        assert d["criterion"] == "rel_diff > tolerance"
        assert "analytic" in d and "monte_carlo" in d and "rel_diff" in d

    # 同一批合约不收紧时整体成功 —— 证明结果差异确实由请求容差驱动
    ok = engine.price_batch(contracts, PricingMethod.CROSS_CHECK)
    assert ok.failed == 0


@pytest.mark.parametrize("bad", ["x", True, -1.0, float("nan")])
def test_batch_invalid_tolerance_rejected(engine, bad):
    with pytest.raises(PricingError) as exc:
        BatchPriceRequest(contracts=[make_contract()],
                          cross_check_tolerance=bad)
    assert exc.value.code is ErrorCode.VALIDATION


def test_scenario_request_tolerance_actually_applies(engine):
    c = make_contract()
    shocks = [Shock(spot_pct=0.05), Shock(vol_shift=0.02)]

    # 默认容差：全部成功
    ok = run_scenarios(engine, "s", [c], shocks, PricingMethod.CROSS_CHECK)
    assert all(l.ok for sh in ok.shocks for l in sh.legs)

    # 收紧容差：基准就算不出来 → 每冲击该腿都失败且证据齐全
    with pytest.raises(PricingError):
        # 模型层非法取值同样拒绝
        ScenarioRequest(name="s", contracts=[c], shocks=shocks,
                        cross_check_tolerance="nope")
    tight = run_scenarios(engine, "s", [c], shocks,
                          PricingMethod.CROSS_CHECK, 1e-9)
    for sh in tight.shocks:
        assert sh.failed == 1
        d = sh.legs[0].error.detail
        assert sh.legs[0].error.code == "METHOD_DIVERGENCE"
        assert d["tolerance"] == 1e-9
        assert d["criterion"] == "rel_diff > tolerance"


def test_portfolio_request_tolerance_actually_applies(engine):
    from app.market_registry import MarketRegistry
    c = make_contract()
    req = PortfolioScenarioRequest(
        name="p", contracts=[c], shocks=[Shock(spot_pct=0.02)],
        method=PricingMethod.CROSS_CHECK, cross_check_tolerance=1e-9)
    resp = run_portfolio_scenarios(engine, MarketRegistry(), req)
    # 基准失败：成功 0，失败 1，且错误证据中容差确为请求值
    assert (resp.succeeded, resp.failed) == (0, 1)
    err = resp.shocks[0].legs[0].error
    assert err.code == "METHOD_DIVERGENCE"
    assert err.detail["tolerance"] == 1e-9


def test_scenario_shock_induced_divergence_isolated_with_tight_tolerance(engine):
    # 基准通过（rel_diff~3e-11）；+30% 冲击使标的接近行权价，MC 相对误差增大
    c = make_contract(strike=120.0, time_to_maturity=0.02)
    tol = 1e-7
    resp = run_scenarios(engine, "s", [c], [Shock(spot_pct=0.3)],
                         PricingMethod.CROSS_CHECK, tol)
    leg = resp.shocks[0].legs[0]
    # 基准有价、冲击后腿失败：容差确实参与了冲击后的重估
    assert leg.base_price is not None
    assert not leg.ok and leg.error.code == "METHOD_DIVERGENCE"
    assert leg.error.detail["tolerance"] == tol
    assert leg.error.detail["rel_diff"] > tol
