"""HTTP 接口：兼容根路径、批量定价、反解、曲面查询、情景、错误结构。"""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_root_compatible(client):
    assert client.get("/").json() == {"Hello": "World"}


CONTRACT = {"contract_id": "a", "option_type": "call", "spot": 100.0,
            "strike": 100.0, "time_to_maturity": 1.0, "volatility": 0.2,
            "risk_free_rate": 0.05}


def test_price_single(client):
    r = client.post("/price", json={"contract": CONTRACT, "method": "cross_check"})
    assert r.status_code == 200
    body = r.json()
    assert abs(body["price"] - 10.450583572185565) < 1e-9
    assert set(body["greeks"]) == {"delta", "gamma", "vega", "theta", "rho"}


def test_price_invalid_returns_distinct_error(client):
    bad = dict(CONTRACT, time_to_maturity=-1.0)
    r = client.post("/price", json={"contract": bad, "method": "analytic"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION"
    assert err["detail"]["field"] == "time_to_maturity"


def test_batch_partial_failure(client):
    bad = dict(CONTRACT, contract_id="bad", volatility=-0.1)
    r = client.post("/price/batch",
                    json={"contracts": [CONTRACT, bad], "method": "analytic"})
    body = r.json()
    assert (body["succeeded"], body["failed"]) == (1, 1)
    assert body["results"][1]["error"]["code"] == "VALIDATION"


def test_implied_vol_endpoint(client):
    r = client.post("/implied-vol", json={
        "option_type": "call", "spot": 100.0, "strike": 100.0,
        "time_to_maturity": 1.0, "risk_free_rate": 0.05,
        "target_price": 10.450583572185565})
    assert r.status_code == 200
    assert abs(r.json()["implied_vol"] - 0.2) < 1e-6


def test_implied_vol_out_of_bounds(client):
    r = client.post("/implied-vol", json={
        "option_type": "call", "spot": 100.0, "strike": 100.0,
        "time_to_maturity": 1.0, "risk_free_rate": 0.05, "target_price": 500.0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NO_ARBITRAGE_BOUNDS"


def test_surface_query_and_out_of_range(client):
    r = client.get("/surface/vol", params={"maturity": 1.0, "strike": 100.0})
    assert r.status_code == 200 and r.json()["value"] == 0.22
    r = client.get("/surface/vol", params={"maturity": 5.0, "strike": 100.0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "OUT_OF_SURFACE"


def test_scenario_endpoint(client):
    r = client.post("/scenarios", json={
        "name": "stress", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": -0.1, "vol_shift": 0.05}],
        "method": "analytic"})
    assert r.status_code == 200
    body = r.json()
    s = body["shocks"][0]
    assert s["succeeded"] == 1
    leg = s["legs"][0]
    assert abs(s["total_pnl"] - leg["pnl"]) < 1e-12


def test_market_snapshot_publish_and_get(client):
    r = client.post("/market/snapshots",
                    json={"spot": 105.0, "risk_free_rate": 0.04,
                          "dividend_yield": 0.01})
    assert r.status_code == 201
    version = r.json()["version"]
    got = client.get(f"/market/snapshots/{version}")
    assert got.status_code == 200
    assert got.json()["spot"] == 105.0


def test_market_snapshot_unknown_version(client):
    r = client.get("/market/snapshots/v999")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNKNOWN_MARKET_VERSION"


def test_price_with_market_version(client):
    version = client.post("/market/snapshots", json={
        "spot": 105.0, "risk_free_rate": 0.04, "dividend_yield": 0.01
    }).json()["version"]
    contract = {k: v for k, v in CONTRACT.items()
                if k not in ("spot", "risk_free_rate")}
    r = client.post("/price", json={"contract": contract, "method": "analytic",
                                    "market_version": version})
    assert r.status_code == 200
    body = r.json()
    assert body["market_version"] == version
    # 与显式传入相同行情取值的结果一致
    explicit = client.post("/price", json={
        "contract": dict(contract, spot=105.0, risk_free_rate=0.04,
                         dividend_yield=0.01),
        "method": "analytic"}).json()
    assert body["price"] == explicit["price"]


def test_price_market_first_precedence(client):
    version = client.post("/market/snapshots",
                          json={"spot": 120.0, "risk_free_rate": 0.04}
                          ).json()["version"]
    r = client.post("/price", json={
        "contract": CONTRACT, "method": "analytic",
        "market_version": version, "market_precedence": "market_first"})
    assert r.status_code == 200
    # 行情覆盖合约的 spot=100 → 价格应等于 spot=120 的解析价
    expected = client.post("/price", json={
        "contract": dict(CONTRACT, spot=120.0, risk_free_rate=0.04),
        "method": "analytic"}).json()
    assert r.json()["price"] == expected["price"]


def test_portfolio_scenarios_endpoint(client):
    r = client.post("/portfolio/scenarios", json={
        "name": "pf",
        "contracts": [CONTRACT, dict(CONTRACT, contract_id="b", strike=110.0,
                                     quantity=2.0),
                      dict(CONTRACT, contract_id="bad", volatility=-0.1)],
        "shocks": [{"spot_pct": -0.1}, {"spot_pct": 0.05, "vol_shift": 0.02}],
        "method": "analytic"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["greeks"]) == {"delta", "gamma", "vega", "theta", "rho"}
    for shock in body["shocks"]:
        assert (shock["succeeded"], shock["failed"]) == (2, 1)
        ok_legs = [leg for leg in shock["legs"] if leg["ok"]]
        assert abs(shock["portfolio_value"]
                   - sum(leg["shocked_price"] for leg in ok_legs)) < 1e-9
        assert abs(shock["total_pnl"]
                   - sum(leg["pnl"] for leg in ok_legs)) < 1e-9
        bad_leg = [leg for leg in shock["legs"] if leg["contract_id"] == "bad"][0]
        assert bad_leg["error"]["code"] == "VALIDATION"


def test_batch_tightened_tolerance_reports_divergence(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    body = r.json()
    assert body["failed"] == 1
    err = body["results"][0]["error"]
    assert err["code"] == "METHOD_DIVERGENCE"
    assert err["detail"]["tolerance"] == 1e-9
    assert err["detail"]["tolerance_source"] == "request"
    assert "analytic" in err["detail"] and "monte_carlo" in err["detail"]
