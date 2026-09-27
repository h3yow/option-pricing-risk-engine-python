"""并发：批量定价与情景重估引用同一行情版本时结果逐位一致、无半更新。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.batching import run_batch_pricing
from app.config import load_settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.market_registry import MarketRegistry
from app.models import (MarketPrecedence, MarketSnapshot, PricingMethod,
                        Shock)
from app.scenarios import run_scenarios
from tests.conftest import make_contract


@pytest.fixture()
def engine():
    return PricingEngine(load_settings({}))


@pytest.fixture()
def registry():
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(
        snapshot_id="cv1", spot=103.5, risk_free_rate=0.031,
        dividend_yield=0.012, volatility=0.237))
    return reg


def _contracts():
    return [make_contract(contract_id=f"c{i}", strike=92 + i * 3,
                          quantity=1.0 + 0.25 * i) for i in range(6)]


def test_concurrent_batch_same_version_identical(engine, registry):
    def work(_):
        return run_batch_pricing(
            engine, registry, _contracts(), PricingMethod.ANALYTIC,
            market_version="cv1",
            precedence=MarketPrecedence.SNAPSHOT).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    first = runs[0]
    for i, r in enumerate(runs[1:], start=1):
        assert r == first, f"批量定价第 {i} 个并发请求结果不一致"


def test_batch_same_version_sequential_equals_concurrent(engine, registry):
    def call():
        return run_batch_pricing(
            engine, registry, _contracts(), PricingMethod.CROSS_CHECK,
            market_version="cv1",
            precedence=MarketPrecedence.SNAPSHOT).model_dump()

    seq = call()
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: call(), range(16)))
    assert all(r == seq for r in runs), "先发/并发批量请求引用同一版本必须完全一致"


def test_concurrent_scenarios_same_version_identical(engine, registry):
    contracts = _contracts()
    shocks = [Shock(spot_pct=-0.04), Shock(vol_shift=0.02, rate_shift=0.005)]

    def work(_):
        return run_scenarios(
            engine, "s", contracts, shocks, PricingMethod.ANALYTIC,
            snapshot=registry.get("cv1"),
            precedence=MarketPrecedence.SNAPSHOT).model_dump()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    first = runs[0]
    for i, r in enumerate(runs[1:], start=1):
        assert r == first, f"情景重估第 {i} 个并发请求结果不一致"


def test_concurrent_unknown_version_all_rejected(engine, registry):
    def work(_):
        try:
            run_batch_pricing(
                engine, registry, _contracts(), PricingMethod.ANALYTIC,
                market_version="missing",
                precedence=MarketPrecedence.SNAPSHOT)
            return "OK"
        except PricingError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(work, range(16)))
    assert set(outcomes) == {ErrorCode.VERSION_NOT_FOUND}


def test_concurrent_clash_publish_never_affects_bound_reads(engine, registry):
    # 引用既有版本的批量/情景读取，与并发的异内容覆盖同时进行：
    # 覆盖全部被拒，读取结果不漂移（无半更新）。
    def bound_read(_):
        batch = run_batch_pricing(
            engine, registry, _contracts(), PricingMethod.ANALYTIC,
            market_version="cv1",
            precedence=MarketPrecedence.SNAPSHOT).model_dump()
        scen = run_scenarios(
            engine, "s", _contracts(), [Shock(spot_pct=0.01)],
            PricingMethod.ANALYTIC, snapshot=registry.get("cv1"),
            precedence=MarketPrecedence.SNAPSHOT).model_dump()
        return batch, scen

    before = bound_read(0)

    def clash(_):
        try:
            registry.publish(MarketSnapshot(
                snapshot_id="cv1", spot=999.0, risk_free_rate=0.9,
                dividend_yield=0.5, volatility=0.77))
            return "OVERWRITTEN"
        except PricingError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        clashes = list(pool.map(clash, range(16)))
    assert set(clashes) == {ErrorCode.VERSION_CONFLICT}

    with ThreadPoolExecutor(max_workers=8) as pool:
        after = list(pool.map(bound_read, range(16)))
    assert all(r == before for r in after), "版本半更新导致绑定读取漂移"
