"""HTTP：行情版本发布与组合情景端点、错误结构、与既有接口并存。"""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


SNAP = {"snapshot_id": "v1", "spot": 105.0, "risk_free_rate": 0.03,
        "dividend_yield": 0.01, "volatility": 0.25}

CONTRACT_A = {"contract_id": "a", "option_type": "call", "spot": 100.0,
              "strike": 100.0, "time_to_maturity": 1.0, "volatility": 0.2,
              "risk_free_rate": 0.05, "quantity": 2.0}
CONTRACT_B = {"contract_id": "b", "option_type": "put", "spot": 100.0,
              "strike": 110.0, "time_to_maturity": 0.5, "volatility": 0.3,
              "risk_free_rate": 0.05}


def _publish(client, snap=SNAP):
    return client.post("/market/versions", json={"snapshot": snap})


def test_publish_version_is_frozen_and_idempotent(client):
    assert _publish(client).status_code == 200
    assert _publish(client).json() == {"snapshot_id": "v1", "frozen": True}
    r = client.post("/market/versions",
                    json={"snapshot": dict(SNAP, spot=106.0)})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_CONFLICT"


def test_portfolio_endpoint_aggregation_and_versions(client):
    _publish(client)
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "market_version": "v1", "precedence": "snapshot",
        "method": "analytic", "contracts": [CONTRACT_A, CONTRACT_B],
        "shocks": [{"spot_pct": -0.05}, {"spot_pct": 0.03, "vol_shift": 0.02}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_version"] == "v1"
    assert body["precedence"] == "snapshot"
    for s in body["shocks"]:
        good = [l for l in s["legs"] if l["ok"]]
        assert s["succeeded"] == 2
        assert abs(s["total_pnl"] - sum(l["pnl"] for l in good)) < 1e-9
        assert abs(s["total_value"]
                   - sum(l["shocked_price"] for l in good)) < 1e-9
        for key in ("delta", "gamma", "vega", "theta", "rho"):
            assert abs(s["greeks"][key]
                       - sum(l["greeks"][key] for l in good)) < 1e-12
        assert all(l["market_version"] == "v1" for l in good)


def test_portfolio_default_precedence_is_contract(client):
    _publish(client)
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "market_version": "v1", "method": "analytic",
        "contracts": [CONTRACT_A], "shocks": [{"spot_pct": 0.0}]})
    assert r.status_code == 200
    body = r.json()
    assert body["precedence"] == "contract"
    # 合约自带 spot=100 生效，仅版本标注为 v1
    leg = body["shocks"][0]["legs"][0]
    assert leg["market_version"] == "v1"
    single = client.post("/price", json={"contract": CONTRACT_A,
                                         "method": "analytic"}).json()
    assert abs(leg["shocked_price"] - single["price"] * 2.0) < 1e-10


def test_portfolio_unknown_version_422(client):
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "market_version": "ghost", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": 0.0}]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_NOT_FOUND"


def test_portfolio_limit_rejected_without_partial_results(client):
    contracts = [dict(CONTRACT_A, contract_id=f"c{i}") for i in range(257)]
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "contracts": contracts,
        "shocks": [{"spot_pct": 0.0}]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "LIMIT_EXCEEDED"
    # 服务继续可用，无残留
    assert client.get("/").json() == {"Hello": "World"}


def test_portfolio_leg_failure_isolated(client):
    _publish(client)
    bad = dict(CONTRACT_A, contract_id="bad", volatility=-0.2)
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "contracts": [CONTRACT_A, bad],
        "shocks": [{"spot_pct": 0.1}]})
    s = r.json()["shocks"][0]
    assert (s["succeeded"], s["failed"]) == (1, 1)
    assert s["legs"][1]["error"]["code"] == "VALIDATION"
    assert abs(s["total_pnl"] - s["legs"][0]["pnl"]) < 1e-12


def test_existing_endpoints_still_work(client):
    assert client.get("/").json() == {"Hello": "World"}
    r = client.post("/scenarios", json={
        "name": "stress", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": -0.1}], "method": "analytic"})
    assert r.status_code == 200
