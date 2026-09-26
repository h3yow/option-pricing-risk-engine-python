"""行情版本绑定：优先级、结果可溯源、并发下同版本结果完全一致。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import ErrorCode, PricingError
from app.market import MarketStore, resolve_contract
from app.models import MarketPrecedence, PricingMethod
from tests.conftest import make_contract


@pytest.fixture()
def store():
    return MarketStore()


def test_publish_and_get_roundtrip(store):
    snap = store.publish(spot=105.0, risk_free_rate=0.04, dividend_yield=0.01)
    assert snap.version == "v1"
    got = store.get("v1")
    assert got is snap
    assert (got.spot, got.risk_free_rate, got.dividend_yield) == (105.0, 0.04, 0.01)


def test_versions_monotonic_and_independent(store):
    v1 = store.publish(spot=100.0, risk_free_rate=0.05)
    v2 = store.publish(spot=110.0, risk_free_rate=0.03)
    assert v1.version != v2.version
    # 发布新版本不影响旧版本内容
    assert store.get(v1.version).spot == 100.0
    assert store.get(v2.version).spot == 110.0


def test_unknown_version_rejected(store):
    store.publish(spot=100.0, risk_free_rate=0.05)
    with pytest.raises(PricingError) as exc_info:
        store.get("v999")
    assert exc_info.value.code is ErrorCode.UNKNOWN_MARKET_VERSION
    assert exc_info.value.detail["version"] == "v999"


def test_snapshot_immutable(store):
    snap = store.publish(spot=100.0, risk_free_rate=0.05)
    with pytest.raises(Exception):
        snap.spot = 999.0
    assert store.get(snap.version).spot == 100.0


def test_contract_first_fills_missing_only():
    snap_data = dict(spot=105.0, risk_free_rate=0.04, dividend_yield=0.01)
    from app.models import MarketSnapshot
    snap = MarketSnapshot(version="v1", **snap_data)
    c = make_contract(spot=100.0, risk_free_rate=None, dividend_yield=None)
    resolved = resolve_contract(c, snap, MarketPrecedence.CONTRACT_FIRST)
    assert resolved.spot == 100.0            # 合约自带取值优先
    assert resolved.risk_free_rate == 0.04   # 缺失字段由行情补齐
    assert resolved.dividend_yield == 0.01
    # 原合约不被修改
    assert c.risk_free_rate is None


def test_market_first_overrides_contract_values():
    from app.models import MarketSnapshot
    snap = MarketSnapshot(version="v1", spot=105.0, risk_free_rate=0.04,
                          dividend_yield=0.01)
    c = make_contract(spot=100.0, risk_free_rate=0.09, dividend_yield=0.0)
    resolved = resolve_contract(c, snap, MarketPrecedence.MARKET_FIRST)
    assert (resolved.spot, resolved.risk_free_rate, resolved.dividend_yield) \
        == (105.0, 0.04, 0.01)


def test_result_carries_market_version(engine, store):
    snap = store.publish(spot=100.0, risk_free_rate=0.05)
    r = engine.price_one(make_contract(spot=None, risk_free_rate=None),
                         PricingMethod.ANALYTIC, market=snap)
    assert r.market_version == snap.version
    r2 = engine.price_one(make_contract(), PricingMethod.ANALYTIC)
    assert r2.market_version is None  # 未绑定行情时明确为空


def test_same_version_consistent_results(engine, store):
    """引用同一版本的多个请求（先后与并发）结果完全一致。"""
    snap = store.publish(spot=102.0, risk_free_rate=0.045, dividend_yield=0.01)
    contracts = [make_contract(contract_id=f"c{i}", spot=None,
                               risk_free_rate=None, dividend_yield=None,
                               strike=90 + i * 5)
                 for i in range(6)]

    def work(_):
        resp = engine.price_batch(contracts, PricingMethod.CROSS_CHECK,
                                  market=snap)
        return [r.result.price for r in resp.results]

    expected = work(None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(work, range(32)))
    for i, prices in enumerate(runs):
        assert prices == expected, f"第 {i} 次并发请求结果漂移: {prices}"


def test_no_half_updated_market_reads(engine, store):
    """发布新版本期间，引用旧版本的并发请求结果不受任何影响。"""
    v1 = store.publish(spot=100.0, risk_free_rate=0.05)
    c = make_contract(spot=None, risk_free_rate=None)
    expected = engine.price_one(c, PricingMethod.ANALYTIC, market=v1).price

    def read_old(_):
        snap = store.get(v1.version)
        return engine.price_one(c, PricingMethod.ANALYTIC, market=snap).price

    def publish_new(i):
        store.publish(spot=100.0 + i, risk_free_rate=0.05)

    with ThreadPoolExecutor(max_workers=8) as pool:
        reads = list(pool.map(read_old, range(64)))
        list(pool.map(publish_new, range(16)))
    assert all(p == expected for p in reads), "并发发布期间旧版本读取结果漂移"
    assert store.get(v1.version).spot == 100.0
