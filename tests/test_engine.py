"""引擎：双途径容差、批量上限、部分失败隔离、结果可复现。"""
import pytest

from app.config import Settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.models import OptionType, PricingMethod
from app.pricing.black_scholes import bs_price
from app.pricing.monte_carlo import mc_price
from tests.conftest import make_contract


def test_two_methods_agree_within_tolerance(contract, settings):
    a = bs_price(contract)
    m = mc_price(contract, settings.mc_paths, settings.mc_seed)
    rel = abs(a - m) / max(abs(a), 1.0)
    assert rel <= settings.cross_check_tolerance, (
        f"两途径分歧: 解析={a}, 蒙特卡洛={m}, 相对差={rel:.4%} "
        f"> 容差 {settings.cross_check_tolerance:.4%}")


def test_cross_check_returns_price_when_agree(engine, contract):
    r = engine.price_one(contract, PricingMethod.CROSS_CHECK)
    assert r.method == "cross_check"
    assert abs(r.price - bs_price(contract)) < 1e-12


def test_cross_check_divergence_reported_not_silent():
    # 人为收紧容差到不可能达到 → 必须明确报分歧
    settings = Settings(cross_check_tolerance=1e-9)
    engine = PricingEngine(settings)
    c = make_contract()
    with pytest.raises(PricingError) as exc_info:
        engine.price_one(c, PricingMethod.CROSS_CHECK)
    err = exc_info.value
    assert err.code is ErrorCode.METHOD_DIVERGENCE, (
        f"容差 1e-9 下两途径必然分歧，应报 METHOD_DIVERGENCE，实际 {err.code}")
    assert "analytic" in err.detail and "monte_carlo" in err.detail


def test_batch_partial_failure_isolated(engine):
    good = make_contract(contract_id="good")
    bad = make_contract(contract_id="bad", time_to_maturity=-1.0)
    resp = engine.price_batch([good, bad], PricingMethod.ANALYTIC)
    assert (resp.succeeded, resp.failed) == (1, 1), (
        f"应 1 成功 1 失败，实际 {resp.succeeded}/{resp.failed}: {resp}")
    assert resp.results[0].ok and resp.results[0].result.price > 0
    assert not resp.results[1].ok
    assert resp.results[1].error.code == "VALIDATION"


def test_batch_size_limit_rejects_whole_batch():
    settings = Settings(max_batch_size=2)
    engine = PricingEngine(settings)
    contracts = [make_contract(contract_id=str(i)) for i in range(3)]
    with pytest.raises(PricingError) as exc_info:
        engine.price_batch(contracts, PricingMethod.ANALYTIC)
    assert exc_info.value.code is ErrorCode.LIMIT_EXCEEDED


def test_monte_carlo_reproducible(contract, settings):
    p1 = mc_price(contract, settings.mc_paths, settings.mc_seed)
    p2 = mc_price(contract, settings.mc_paths, settings.mc_seed)
    assert p1 == p2, f"相同种子两次结果应一致: {p1} vs {p2}"


def test_put_call_parity(contract):
    put = make_contract(option_type=OptionType.PUT)
    call = make_contract(option_type=OptionType.CALL)
    import math
    lhs = bs_price(call) - bs_price(put)
    rhs = 100.0 - 100.0 * math.exp(-0.05)
    assert abs(lhs - rhs) < 1e-10, f"看跌看涨平价不成立: {lhs} vs {rhs}"


def test_default_cross_check_succeeds_over_common_ranges(engine):
    """默认设置下，覆盖深度实/虚值、短/长到期的常见参数批量请求整体成功。"""
    contracts = []
    for ot in (OptionType.CALL, OptionType.PUT):
        for strike in (20.0, 50.0, 80.0, 100.0, 120.0, 150.0, 200.0, 300.0):
            for ttm in (1 / 365, 7 / 365, 0.25, 1.0, 5.0, 10.0):
                for vol in (0.05, 0.2, 0.5, 0.8):
                    contracts.append(make_contract(
                        contract_id=f"{ot.value}-{strike}-{ttm}-{vol}",
                        option_type=ot, strike=strike,
                        time_to_maturity=ttm, volatility=vol))
    resp_failed = []
    succeeded = 0
    # 分块提交，尊重批量上限
    for i in range(0, len(contracts), engine.settings.max_batch_size):
        resp = engine.price_batch(contracts[i:i + engine.settings.max_batch_size],
                                  PricingMethod.CROSS_CHECK)
        succeeded += resp.succeeded
        resp_failed.extend(r for r in resp.results if not r.ok)
    assert not resp_failed, (
        "默认设置下正常合约不应被判为途径分歧: "
        + "; ".join(f"{r.contract_id}: {r.error.code} {r.error.detail}"
                    for r in resp_failed))
    assert succeeded == len(contracts)


def test_request_tolerance_tightened_reports_divergence(engine):
    """请求方显式收紧容差到无法满足 → 报分歧，并给出双方数值/容差/判定依据。"""
    c = make_contract()
    with pytest.raises(PricingError) as exc_info:
        engine.price_one(c, PricingMethod.CROSS_CHECK, tolerance=1e-9)
    err = exc_info.value
    assert err.code is ErrorCode.METHOD_DIVERGENCE
    d = err.detail
    assert d["tolerance"] == 1e-9
    assert d["tolerance_source"] == "request"
    assert d["analytic"] > 0 and d["monte_carlo"] > 0
    assert d["rel_diff"] > d["tolerance"]
    assert "criterion" in d, f"应给出判定依据: {d}"


def test_default_tolerance_source_tagged():
    settings = Settings(cross_check_tolerance=1e-9)
    engine = PricingEngine(settings)
    with pytest.raises(PricingError) as exc_info:
        engine.price_one(make_contract(), PricingMethod.CROSS_CHECK)
    assert exc_info.value.detail["tolerance_source"] == "default"
    assert exc_info.value.detail["tolerance"] == 1e-9


def test_batch_item_divergence_isolated(engine):
    """批量中请求级容差收紧导致的分歧按笔隔离，不影响其余合约。"""
    contracts = [make_contract(contract_id="a"), make_contract(contract_id="b")]
    resp = engine.price_batch(contracts, PricingMethod.CROSS_CHECK,
                              tolerance=1e-9)
    assert (resp.succeeded, resp.failed) == (0, 2)
    assert all(r.error.code == "METHOD_DIVERGENCE" for r in resp.results)
    resp_ok = engine.price_batch(contracts, PricingMethod.CROSS_CHECK)
    assert resp_ok.failed == 0
