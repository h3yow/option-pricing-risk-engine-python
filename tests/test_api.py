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
