"""HTTP：批量定价/情景重估的版本绑定、未知版本整体拒绝、请求级容差入口。"""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


SNAP = {"snapshot_id": "bind-v1", "spot": 105.0, "risk_free_rate": 0.03,
        "dividend_yield": 0.01, "volatility": 0.25}
CONTRACT_A = {"contract_id": "a", "option_type": "call", "spot": 100.0,
              "strike": 100.0, "time_to_maturity": 1.0, "volatility": 0.2,
              "risk_free_rate": 0.05}


def _publish(client, snap=SNAP):
    return client.post("/market/versions", json={"snapshot": snap})


# ---------- 批量定价 ----------

def test_batch_version_binding_and_precedence_api(client):
    _publish(client)
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT_A], "method": "analytic",
        "market_version": "bind-v1", "precedence": "snapshot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_version"] == "bind-v1"
    assert body["precedence"] == "snapshot"
    item = body["results"][0]
    assert item["ok"] and item["market_version"] == "bind-v1"
    # 按版本 spot=105/vol=25% 等取值，而非合约 100/20%
    single = client.post("/price", json={
        "contract": dict(CONTRACT_A, spot=105.0, volatility=0.25,
                         risk_free_rate=0.03, dividend_yield=0.01),
        "method": "analytic"}).json()
    assert abs(item["result"]["price"] - single["price"]) < 1e-10


def test_batch_unknown_version_rejects_entire_request_no_partial(client):
    # 引用不存在版本：整体 422，且不应出现任何逐笔结果
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT_A, CONTRACT_A], "method": "analytic",
        "market_version": "ghost"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_NOT_FOUND"
    assert "results" not in r.json()
    # 服务无残留：随后不绑定的请求照常
    ok = client.post("/price/batch",
                     json={"contracts": [CONTRACT_A], "method": "analytic"})
    assert ok.status_code == 200
    assert ok.json()["results"][0]["market_version"] is None


def test_batch_default_unbound_response_shape_unchanged(client):
    r = client.post("/price/batch",
                    json={"contracts": [CONTRACT_A], "method": "analytic"})
    body = r.json()
    assert body["market_version"] is None
    assert body["precedence"] == "contract"
    assert body["results"][0]["market_version"] is None


def test_batch_invalid_tolerance_rejected_not_silently_default(client):
    for bad in ("0.01", True, -0.02, 0, "nan"):
        r = client.post("/price/batch", json={
            "contracts": [CONTRACT_A], "method": "cross_check",
            "cross_check_tolerance": bad})
        assert r.status_code == 422, f"非法容差 {bad!r} 必须拒绝"
        err = r.json()["error"]
        assert err["code"] == "VALIDATION"
        assert err["detail"]["field"] == "cross_check_tolerance"


def test_batch_tight_tolerance_diverges_with_evidence_api(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT_A], "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 200  # 逐笔隔离：批量本身仍返回
    item = r.json()["results"][0]
    assert item["ok"] is False
    d = item["error"]["detail"]
    assert item["error"]["code"] == "METHOD_DIVERGENCE"
    assert d["tolerance"] == 1e-9
    assert d["criterion"] == "rel_diff > tolerance"
    assert "analytic" in d and "monte_carlo" in d and "rel_diff" in d


# ---------- 情景重估 ----------

def test_scenario_version_binding_api(client):
    _publish(client)
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": -0.05}], "method": "analytic",
        "market_version": "bind-v1", "precedence": "snapshot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_version"] == "bind-v1"
    assert body["precedence"] == "snapshot"
    leg = body["shocks"][0]["legs"][0]
    assert leg["ok"] and leg["market_version"] == "bind-v1"
    single = client.post("/price", json={
        "contract": dict(CONTRACT_A, spot=105.0 * 0.95, volatility=0.25,
                         risk_free_rate=0.03, dividend_yield=0.01),
        "method": "analytic"}).json()
    assert abs(leg["shocked_price"] - single["price"]) < 1e-10


def test_scenario_unknown_version_rejects_entire_request(client):
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": 0.0}], "method": "analytic",
        "market_version": "ghost"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_NOT_FOUND"
    assert "shocks" not in r.json()
    # 无残留：后续请求正常
    ok = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": 0.0}], "method": "analytic"})
    assert ok.status_code == 200


def test_scenario_invalid_tolerance_rejected_api(client):
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT_A],
        "shocks": [{"spot_pct": 0.0}], "method": "cross_check",
        "cross_check_tolerance": "tight"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


def test_single_price_tight_tolerance_api(client):
    # 单笔 /price 同样接受请求级容差并给出分歧证据
    r = client.post("/price", json={
        "contract": CONTRACT_A, "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "METHOD_DIVERGENCE"
    assert err["detail"]["tolerance"] == 1e-9
