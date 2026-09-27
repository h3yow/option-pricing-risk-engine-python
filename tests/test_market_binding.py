"""批量定价与情景重估的行情版本绑定/优先级：复用同一份冻结快照、逐笔标注、
未知版本整体拒绝、缺字段逐笔失败、不绑定保持默认。"""
import pytest

from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.market_registry import MarketRegistry
from app.models import (MarketPrecedence, MarketSnapshot, PricingMethod,
                        Shock)
from app.pricing.black_scholes import bs_price
from app.scenarios import run_scenarios
from tests.conftest import make_contract


@pytest.fixture()
def registry():
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(
        snapshot_id="v1", spot=105.0, risk_free_rate=0.03,
        dividend_yield=0.01, volatility=0.25))
    reg.publish(MarketSnapshot(
        snapshot_id="novol", spot=105.0, risk_free_rate=0.03,
        dividend_yield=0.01))
    return reg


# ---------- 批量定价 ----------

def test_batch_snapshot_precedence_overrides_contract(engine, registry):
    c = make_contract(contract_id="a", spot=100.0, volatility=0.2,
                      risk_free_rate=0.05)
    snap = registry.get("v1")
    resp = engine.price_batch([c], PricingMethod.ANALYTIC, None,
                              snap, MarketPrecedence.SNAPSHOT)
    assert resp.market_version == "v1"
    assert resp.precedence == "snapshot"
    item = resp.results[0]
    assert item.ok and item.market_version == "v1"
    # 必须按版本取值（105/3%/1%/25%），而非合约自带（100/5%/0/20%）
    resolved = make_contract(contract_id="a", spot=105.0, strike=100.0,
                             volatility=0.25, risk_free_rate=0.03,
                             dividend_yield=0.01)
    assert abs(item.result.price - bs_price(resolved)) < 1e-12
    assert abs(item.result.price - bs_price(c)) > 1e-6


def test_batch_contract_precedence_keeps_values_but_marks_version(
        engine, registry):
    c = make_contract(contract_id="a", spot=100.0, volatility=0.2,
                      risk_free_rate=0.05)
    snap = registry.get("v1")
    resp = engine.price_batch([c], PricingMethod.ANALYTIC, None,
                              snap, MarketPrecedence.CONTRACT)
    item = resp.results[0]
    assert item.ok and item.market_version == "v1"
    assert resp.market_version == "v1" and resp.precedence == "contract"
    assert abs(item.result.price - bs_price(c)) < 1e-12


def test_batch_unbound_default_unchanged(engine, registry):
    c = make_contract()
    resp = engine.price_batch([c], PricingMethod.ANALYTIC)
    assert resp.market_version is None and resp.precedence == "contract"
    item = resp.results[0]
    assert item.ok and item.market_version is None
    assert abs(item.result.price - bs_price(c)) < 1e-12


def test_batch_missing_volatility_is_per_leg_failure(engine, registry):
    good = make_contract(contract_id="g")
    snap = registry.get("novol")
    resp = engine.price_batch([good], PricingMethod.ANALYTIC, None,
                              snap, MarketPrecedence.SNAPSHOT)
    assert resp.failed == 1
    err = resp.results[0].error
    assert err.code == "MARKET_DATA_MISSING"
    assert err.detail["field"] == "volatility"
    # 失败腿仍标注它试图使用的版本
    assert resp.results[0].market_version == "novol"


def test_batch_snapshot_shared_identical_across_contracts(engine, registry):
    # 同一请求内所有合约共享同一份快照取值：改 spot 影响全部腿
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 5)
                 for i in range(4)]
    snap = registry.get("v1")
    resp = engine.price_batch(contracts, PricingMethod.ANALYTIC, None,
                              snap, MarketPrecedence.SNAPSHOT)
    assert resp.failed == 0
    for c, item in zip(contracts, resp.results):
        resolved = make_contract(
            contract_id=c.contract_id, spot=105.0, strike=c.strike,
            time_to_maturity=c.time_to_maturity, volatility=0.25,
            risk_free_rate=0.03, dividend_yield=0.01,
            quantity=c.quantity)
        assert item.market_version == "v1"
        assert abs(item.result.price - bs_price(resolved)) < 1e-12


# ---------- 情景重估 ----------

def test_scenario_snapshot_precedence_overrides_contract(engine, registry):
    c = make_contract(contract_id="a", spot=100.0, volatility=0.2,
                      risk_free_rate=0.05)
    snap = registry.get("v1")
    resp = run_scenarios(engine, "s", [c], [Shock()], PricingMethod.ANALYTIC,
                         None, snap, MarketPrecedence.SNAPSHOT)
    assert resp.market_version == "v1" and resp.precedence == "snapshot"
    leg = resp.shocks[0].legs[0]
    assert leg.ok and leg.market_version == "v1"
    resolved = make_contract(contract_id="a", spot=105.0, volatility=0.25,
                             risk_free_rate=0.03, dividend_yield=0.01)
    assert abs(leg.base_price - bs_price(resolved)) < 1e-12
    assert abs(leg.shocked_price - bs_price(resolved)) < 1e-12


def test_scenario_shock_builds_on_snapshot_values(engine, registry):
    # 冲击应作用在版本行情解析出的合约上：spot 105 * 1.1
    c = make_contract(contract_id="a")
    snap = registry.get("v1")
    resp = run_scenarios(engine, "s", [c], [Shock(spot_pct=0.1)],
                         PricingMethod.ANALYTIC, None, snap,
                         MarketPrecedence.SNAPSHOT)
    leg = resp.shocks[0].legs[0]
    shocked_contract = make_contract(
        contract_id="a", spot=105.0 * 1.1, volatility=0.25,
        risk_free_rate=0.03, dividend_yield=0.01)
    assert abs(leg.shocked_price - bs_price(shocked_contract)) < 1e-12
    assert leg.market_version == "v1"


def test_scenario_contract_precedence_marks_version(engine, registry):
    c = make_contract()
    snap = registry.get("v1")
    resp = run_scenarios(engine, "s", [c], [Shock()], PricingMethod.ANALYTIC,
                         None, snap, MarketPrecedence.CONTRACT)
    leg = resp.shocks[0].legs[0]
    assert leg.market_version == "v1"
    assert abs(leg.shocked_price - bs_price(c)) < 1e-12


def test_scenario_unbound_default_unchanged(engine):
    c = make_contract()
    resp = run_scenarios(engine, "s", [c], [Shock(spot_pct=0.05)],
                         PricingMethod.ANALYTIC)
    assert resp.market_version is None and resp.precedence == "contract"
    leg = resp.shocks[0].legs[0]
    assert leg.ok and leg.market_version is None


def test_scenario_missing_volatility_fails_every_shock(engine, registry):
    c = make_contract()
    snap = registry.get("novol")
    resp = run_scenarios(engine, "s", [c], [Shock(), Shock(spot_pct=0.1)],
                         PricingMethod.ANALYTIC, None, snap,
                         MarketPrecedence.SNAPSHOT)
    assert len(resp.shocks) == 2
    for sh in resp.shocks:
        assert sh.failed == 1
        assert sh.legs[0].error.code == "MARKET_DATA_MISSING"
        assert sh.legs[0].market_version == "novol"
