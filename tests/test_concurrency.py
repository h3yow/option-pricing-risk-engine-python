"""并发：多线程共享引擎/曲面/快照时结果一致，无半更新状态与数值漂移。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import ErrorCode, PricingError
from app.implied_vol import implied_vol
from app.market_registry import MarketRegistry
from app.models import (MarketPrecedence, MarketSnapshot, OptionType,
                        PortfolioScenarioRequest, PricingMethod, Shock)
from app.portfolio import run_portfolio_scenarios
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


def _portfolio_request(version, precedence):
    from tests.conftest import make_contract
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 3,
                               quantity=1.0 + 0.25 * i) for i in range(6)]
    return PortfolioScenarioRequest(
        name="p", contracts=contracts,
        shocks=[Shock(spot_pct=-0.05), Shock(spot_pct=0.03, vol_shift=0.02)],
        method=PricingMethod.ANALYTIC, market_version=version,
        precedence=precedence)


def test_concurrent_requests_same_version_identical(engine):
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="v1", spot=103.0,
                               risk_free_rate=0.04, dividend_yield=0.01,
                               volatility=0.23))
    req = _portfolio_request("v1", MarketPrecedence.SNAPSHOT)

    def work(_):
        return run_portfolio_scenarios(engine, reg, req).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    first = runs[0]
    for i, r in enumerate(runs[1:], start=1):
        assert r == first, f"同一版本第 {i} 个并发请求结果不一致"


def test_version_reuse_identical_sequential_and_concurrent(engine):
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="v9", spot=97.0,
                               risk_free_rate=0.02, volatility=0.28))
    req = _portfolio_request("v9", MarketPrecedence.SNAPSHOT)
    seq = run_portfolio_scenarios(engine, reg, req).model_dump()

    def work(_):
        return run_portfolio_scenarios(engine, reg, req).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        concurrent = list(pool.map(work, range(16)))
    for r in concurrent:
        assert r == seq, "先发/并发请求引用同一版本结果必须完全一致"


def test_published_version_never_mutated_by_republish(engine):
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="v", spot=100.0,
                               risk_free_rate=0.05, volatility=0.2))
    req = _portfolio_request("v", MarketPrecedence.SNAPSHOT)
    before = run_portfolio_scenarios(engine, reg, req).model_dump()

    # 并发尝试以不同内容覆盖同一版本：必须全部拒绝，既有读取不受影响
    def clash(_):
        try:
            reg.publish(MarketSnapshot(snapshot_id="v", spot=200.0,
                                       risk_free_rate=0.9, volatility=0.7))
            return "OVERWRITTEN"
        except PricingError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(clash, range(16)))
    assert set(outcomes) == {ErrorCode.VERSION_CONFLICT}

    def read(_):
        return run_portfolio_scenarios(engine, reg, req).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        after = list(pool.map(read, range(16)))
    assert all(r == before for r in after), "版本被改动后出现半更新/漂移"


def test_concurrent_publish_distinct_versions_then_reads_consistent(engine):
    reg = MarketRegistry()

    def publish(i):
        reg.publish(MarketSnapshot(snapshot_id=f"w{i}", spot=100.0 + i,
                                   risk_free_rate=0.03, volatility=0.2))
        return f"w{i}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(publish, range(32)))
    assert sorted(reg.versions()) == sorted(ids)

    def read(i):
        req = _portfolio_request(f"w{i}", MarketPrecedence.SNAPSHOT)
        return run_portfolio_scenarios(engine, reg, req).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(read, range(32)))
    # 每个版本结果自洽且可复现：同版本再读一遍逐位相等
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows2 = list(pool.map(read, range(32)))
    assert rows == rows2


def test_concurrent_batch_same_version_identical(engine):
    # 批量定价：先发一次，再与并发请求逐位比较（含每笔 market_version 标注）
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="cv", spot=103.0,
                               risk_free_rate=0.04, dividend_yield=0.01,
                               volatility=0.23))
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 3)
                 for i in range(8)]
    snap = reg.get("cv")
    seq = engine.price_batch(contracts, PricingMethod.ANALYTIC, None, snap,
                             MarketPrecedence.SNAPSHOT).model_dump()

    def work(_):
        return engine.price_batch(contracts, PricingMethod.ANALYTIC, None,
                                  snap, MarketPrecedence.SNAPSHOT
                                  ).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    for i, r in enumerate(runs):
        assert r == seq, f"批量引用同一版本第 {i} 个并发请求结果不一致"
    assert all(item["market_version"] == "cv"
               for item in seq["results"])


def test_concurrent_scenarios_same_version_identical(engine):
    # 情景重估：先发一次，再与并发请求逐位比较（含每条腿版本标注）
    from app.models import Shock
    from app.scenarios import run_scenarios
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="sv", spot=97.0,
                               risk_free_rate=0.02, dividend_yield=0.0,
                               volatility=0.28))
    contracts = [make_contract(contract_id=f"c{i}", strike=95 + i * 2,
                               quantity=1.0 - 0.1 * i) for i in range(6)]
    shocks = [Shock(spot_pct=-0.05), Shock(vol_shift=0.02),
              Shock(rate_shift=0.01)]
    snap = reg.get("sv")
    seq = run_scenarios(engine, "s", contracts, shocks,
                        PricingMethod.ANALYTIC, None, snap,
                        MarketPrecedence.SNAPSHOT).model_dump()

    def work(_):
        return run_scenarios(engine, "s", contracts, shocks,
                             PricingMethod.ANALYTIC, None, snap,
                             MarketPrecedence.SNAPSHOT).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    for i, r in enumerate(runs):
        assert r == seq, f"情景引用同一版本第 {i} 个并发请求结果不一致"
    assert all(leg["market_version"] == "sv"
               for sh in seq["shocks"] for leg in sh["legs"])


def test_concurrent_same_version_never_reads_half_updated(engine):
    # 并发尝试覆盖已发布版本必须全部被拒；期间与之后的批量/情景读取不漂移
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(snapshot_id="frozen", spot=100.0,
                               risk_free_rate=0.05, volatility=0.2))
    contracts = [make_contract(contract_id=f"c{i}", strike=95 + i)
                 for i in range(4)]
    snap = reg.get("frozen")
    before_batch = engine.price_batch(
        contracts, PricingMethod.ANALYTIC, None, snap,
        MarketPrecedence.SNAPSHOT).model_dump()
    from app.models import Shock
    from app.scenarios import run_scenarios
    shocks = [Shock(spot_pct=0.03)]
    before_scen = run_scenarios(engine, "s", contracts, shocks,
                                PricingMethod.ANALYTIC, None, snap,
                                MarketPrecedence.SNAPSHOT).model_dump()

    def clash(_):
        try:
            reg.publish(MarketSnapshot(snapshot_id="frozen", spot=200.0,
                                       risk_free_rate=0.9, volatility=0.7))
            return "OVERWRITTEN"
        except PricingError as e:
            return e.code

    def read_batch(_):
        return engine.price_batch(
            contracts, PricingMethod.ANALYTIC, None,
            reg.get("frozen"), MarketPrecedence.SNAPSHOT).model_dump()

    def read_scen(_):
        return run_scenarios(engine, "s", contracts, shocks,
                             PricingMethod.ANALYTIC, None, reg.get("frozen"),
                             MarketPrecedence.SNAPSHOT).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        clashes = list(pool.map(clash, range(16)))
        batches = list(pool.map(read_batch, range(16)))
        scens = list(pool.map(read_scen, range(16)))
    assert set(clashes) == {ErrorCode.VERSION_CONFLICT}
    assert all(b == before_batch for b in batches)
    assert all(s == before_scen for s in scens)
