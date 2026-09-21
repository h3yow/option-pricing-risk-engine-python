"""并发：多线程共享引擎/曲面/快照时结果一致，无半更新状态与数值漂移。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.implied_vol import implied_vol
from app.models import OptionType, PricingMethod
from app.pricing.black_scholes import bs_price
from app.surface import Surface
from tests.conftest import make_contract


def test_concurrent_pricing_consistent(engine):
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i) for i in range(8)]
    expected = [bs_price(c) for c in contracts]

    def work(n):
        return [engine.price_one(c, PricingMethod.ANALYTIC).price for c in contracts]

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(16)))
    for i, prices in enumerate(runs):
        assert prices == expected, f"第 {i} 次并发运行结果漂移: {prices} != {expected}"


def test_concurrent_surface_query_consistent():
    s = Surface([0.5, 1.0, 2.0], [90.0, 100.0, 110.0],
                [[0.25, 0.20, 0.22], [0.28, 0.23, 0.24], [0.30, 0.26, 0.27]])
    expected = s.query(1.3, 97.5)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: s.query(1.3, 97.5), range(200)))
    assert all(r == expected for r in results)


def test_concurrent_implied_vol_consistent():
    c = make_contract(volatility=0.25)
    target = bs_price(c)

    def solve(_):
        iv, _, _ = implied_vol(OptionType.CALL, 100.0, 100.0, 1.0, 0.05, 0.0,
                               target, max_iterations=200, tolerance=1e-8)
        return iv

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(solve, range(64)))
    assert len(set(results)) == 1, f"并发反解结果不一致: {set(results)}"
    assert abs(results[0] - 0.25) < 1e-6
