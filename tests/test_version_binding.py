"""批量定价与情景重估的行情版本绑定：优先级、逐笔版本标注、未知版本整体拒绝。"""
import pytest
from fastapi.testclient import TestClient

from main import app

# 使用独立 id，避免与其他测试模块共享 app.state 仓库时互相污染
FULL = {"snapshot_id": "bind-full", "spot": 105.0,
        "risk_free_rate": 0.03, "dividend_yield": 0.01, "volatility": 0.25}
NOVOL = {"snapshot_id": "bind-novol", "spot": 105.0,
         "risk_free_rate": 0.03, "dividend_yield": 0.01}

CONTRACT = {"contract_id": "a", "option_type": "call", "spot": 100.0,
            "strike": 100.0, "time_to_maturity": 1.0, "volatility": 0.2,
            "risk_free_rate": 0.05, "dividend_yield": 0.0}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        assert c.post("/market/versions",
                      json={"snapshot": FULL}).status_code == 200
        assert c.post("/market/versions",
                      json={"snapshot": NOVOL}).status_code == 200
        yield c


# ---------- 批量定价 ----------

def test_batch_unbound_uses_contract_values_and_marks_none(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "analytic"})
    assert r.status_code == 200
    body = r.json()
    assert body["market_version"] is None
    assert body["results"][0]["market_version"] is None
    single = client.post("/price",
                         json={"contract": CONTRACT, "method": "analytic"}).json()
    assert abs(body["results"][0]["result"]["price"] - single["price"]) < 1e-12


def test_batch_snapshot_precedence_overrides_contract(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "analytic",
        "market_version": "bind-full", "precedence": "snapshot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_version"] == "bind-full"
    item = body["results"][0]
    assert item["ok"] and item["market_version"] == "bind-full"
    resolved = dict(CONTRACT, spot=105.0, risk_free_rate=0.03,
                    dividend_yield=0.01, volatility=0.25)
    expect = client.post("/price",
                         json={"contract": resolved,
                               "method": "analytic"}).json()["price"]
    assert abs(item["result"]["price"] - expect) < 1e-10


def test_batch_contract_precedence_keeps_values_but_marks_version(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "analytic",
        "market_version": "bind-full", "precedence": "contract"})
    body = r.json()
    assert body["market_version"] == "bind-full"
    item = body["results"][0]
    assert item["market_version"] == "bind-full"
    single = client.post("/price",
                         json={"contract": CONTRACT,
                               "method": "analytic"}).json()["price"]
    assert abs(item["result"]["price"] - single) < 1e-12


def test_batch_missing_volatility_is_per_leg_failure_marked(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT, dict(CONTRACT, contract_id="b")],
        "method": "analytic",
        "market_version": "bind-novol", "precedence": "snapshot"})
    body = r.json()
    assert (body["succeeded"], body["failed"]) == (0, 2)
    for item in body["results"]:
        assert item["market_version"] == "bind-novol"
        assert item["error"]["code"] == "MARKET_DATA_MISSING"
        assert item["error"]["detail"]["field"] == "volatility"


def test_batch_unknown_version_rejects_entire_request(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT, CONTRACT], "method": "analytic",
        "market_version": "ghost"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_NOT_FOUND"
    # 无残留部分结果：响应里没有 results
    assert "results" not in r.json()


def test_batch_unknown_version_engine_still_serves(client):
    assert client.post("/price/batch",
                       json={"contracts": [CONTRACT],
                             "method": "analytic"}).status_code == 200


def test_batch_version_and_tolerance_together(client):
    # 版本绑定 + 请求级收紧容差：snapshot 解析后的合约按收紧容差判定
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "cross_check",
        "market_version": "bind-full", "precedence": "snapshot",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["ok"] is False
    assert item["market_version"] == "bind-full"
    assert item["error"]["code"] == "METHOD_DIVERGENCE"
    assert item["error"]["detail"]["tolerance"] == 1e-9


# ---------- 情景重估 ----------

def _scenario_body(**kw):
    body = {"name": "s", "contracts": [CONTRACT],
            "shocks": [{"spot_pct": -0.05}, {"vol_shift": 0.02}],
            "method": "analytic"}
    body.update(kw)
    return body


def test_scenarios_unbound_marks_none(client):
    r = client.post("/scenarios", json=_scenario_body())
    assert r.status_code == 200
    body = r.json()
    assert body["market_version"] is None
    for s in body["shocks"]:
        assert all(leg["market_version"] is None for leg in s["legs"])


def test_scenarios_snapshot_precedence_overrides_contract(client):
    r = client.post("/scenarios", json=_scenario_body(
        market_version="bind-full", precedence="snapshot"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_version"] == "bind-full"
    # 基准（无冲击）核对：用版本行情独立计价
    resolved = dict(CONTRACT, spot=105.0, risk_free_rate=0.03,
                    dividend_yield=0.01, volatility=0.25)
    base_price = client.post("/price",
                             json={"contract": resolved,
                                   "method": "analytic"}).json()["price"]
    for s in body["shocks"]:
        for leg in s["legs"]:
            assert leg["ok"] and leg["market_version"] == "bind-full"
            assert abs(leg["base_price"] - base_price) < 1e-10


def test_scenarios_shocks_apply_on_top_of_snapshot(client):
    # 冲击应作用在版本行情解析后的合约上（spot=105 再 -5%）
    r = client.post("/scenarios", json=_scenario_body(
        market_version="bind-full", precedence="snapshot"))
    shocked_contract = dict(CONTRACT, spot=105.0 * 0.95,
                            risk_free_rate=0.03, dividend_yield=0.01,
                            volatility=0.25)
    expect = client.post("/price",
                         json={"contract": shocked_contract,
                               "method": "analytic"}).json()["price"]
    leg = r.json()["shocks"][0]["legs"][0]
    assert abs(leg["shocked_price"] - expect) < 1e-10


def test_scenarios_contract_precedence_marks_version(client):
    r = client.post("/scenarios", json=_scenario_body(
        market_version="bind-full", precedence="contract"))
    body = r.json()
    assert body["market_version"] == "bind-full"
    base = client.post("/scenarios", json=_scenario_body()).json()
    for s1, s0 in zip(body["shocks"], base["shocks"]):
        assert all(l["market_version"] == "bind-full" for l in s1["legs"])
        for l1, l0 in zip(s1["legs"], s0["legs"]):
            assert abs(l1["shocked_price"] - l0["shocked_price"]) < 1e-12


def test_scenarios_missing_volatility_per_leg_failure_marked(client):
    r = client.post("/scenarios", json=_scenario_body(
        market_version="bind-novol", precedence="snapshot"))
    body = r.json()
    for s in body["shocks"]:
        assert s["succeeded"] == 0
        leg = s["legs"][0]
        assert leg["market_version"] == "bind-novol"
        assert leg["error"]["code"] == "MARKET_DATA_MISSING"


def test_scenarios_unknown_version_rejects_entire_request(client):
    r = client.post("/scenarios", json=_scenario_body(
        market_version="ghost"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VERSION_NOT_FOUND"
    assert "shocks" not in r.json()


def test_scenarios_unknown_version_no_partial_results(client):
    # 被整体拒绝后，服务与仓库仍可正常服务后续请求
    bad = client.post("/scenarios", json=_scenario_body(
        market_version="ghost"))
    assert bad.status_code == 422
    ok = client.post("/scenarios", json=_scenario_body(
        market_version="bind-full", precedence="snapshot"))
    assert ok.status_code == 200
