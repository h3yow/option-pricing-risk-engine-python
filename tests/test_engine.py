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
