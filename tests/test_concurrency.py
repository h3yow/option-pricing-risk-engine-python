"""并发一致性：多线程共享同一引擎/快照/曲面，结果必须逐位一致。"""

import concurrent.futures

import pytest

from option_engine import (
    MarketSnapshot,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
)
from option_engine.scenario import Shock
from option_engine.surface import Surface


def make_contract(**overrides):
    base = dict(
        option_type=OptionType.CALL,
        spot=100.0,
        strike=100.0,
        maturity=1.0,
        volatility=0.2,
        rate=0.05,
    )
    base.update(overrides)
    return OptionContract(**base)


CONTRACTS = {
    "c1": make_contract(),
    "c2": make_contract(option_type=OptionType.PUT, strike=90.0, volatility=0.3),
    "c3": make_contract(spot=120.0, strike=110.0, maturity=0.5, quantity=3.0),
}
SNAPSHOT = MarketSnapshot(
    snapshot_id="snap-1",
    spots={"c1": 101.5},
    vol_overrides={"c2": 0.28},
)
SURFACE = Surface(
    (0.5, 1.0), (90.0, 100.0, 110.0),
    ((0.20, 0.18, 0.22), (0.25, 0.21, 0.26)),
)


@pytest.fixture
def engine():
    return PricingEngine()


def test_concurrent_batch_pricing_identical(engine):
    baseline = engine.price_batch(CONTRACTS, SNAPSHOT, PricingMethod.CROSS_CHECK)

    def work(_):
        return engine.price_batch(CONTRACTS, SNAPSHOT, PricingMethod.CROSS_CHECK)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(work, range(64)))
    for i, out in enumerate(outcomes):
        assert out == baseline, (
            f"run {i}: concurrent result differs from baseline\n"
            f"baseline={baseline}\ngot={out}"
        )


def test_concurrent_implied_vol_identical(engine):
    from option_engine import black_scholes

    c = make_contract(volatility=0.33)
    target = black_scholes.price(c)
    baseline = engine.implied_volatility(c, target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: engine.implied_volatility(c, target), range(64)))
    assert all(o == baseline for o in outcomes)


def test_concurrent_surface_and_scenario_identical(engine):
    shocks = [Shock(spot_shift=s) for s in (-0.2, -0.1, 0.0, 0.1)]
    baseline_q = engine.query_vol_surface(SURFACE, 0.75, 95.0)
    baseline_s = engine.run_scenario(CONTRACTS, shocks)

    def work(i):
        if i % 2 == 0:
            return engine.query_vol_surface(SURFACE, 0.75, 95.0)
        return engine.run_scenario(CONTRACTS, shocks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(work, range(64)))
    for i, out in enumerate(outcomes):
        if i % 2 == 0:
            assert out == baseline_q, f"surface query drifted in run {i}: {out}"
        else:
            assert out == baseline_s, f"scenario result drifted in run {i}: {out}"
