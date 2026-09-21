"""HTTP 接口层：错误以可区分 reason 返回，既有根路径保持兼容。"""

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

CONTRACT = {
    "option_type": "call",
    "spot": 100.0,
    "strike": 100.0,
    "maturity": 1.0,
    "volatility": 0.2,
    "rate": 0.05,
}


def test_root_compatible():
    assert client.get("/").json() == {"Hello": "World"}


def test_price_ok():
    resp = client.post("/price", json={"contracts": {"a": CONTRACT}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert body["results"][0]["price"] == pytest.approx(10.4506, abs=1e-3)
    assert body["results"][0]["cross_check_diff"] is not None


def test_price_invalid_input_returns_reason():
    bad = dict(CONTRACT, maturity=-1.0)
    resp = client.post("/price", json={"contracts": {"bad": bad}})
    assert resp.status_code == 200  # 批量内单笔失败 => 部分失败信息
    errors = resp.json()["errors"]
    assert errors[0]["reason"] == "non_positive_maturity"


def test_price_unknown_method_rejected():
    resp = client.post("/price", json={"contracts": {"a": CONTRACT}, "method": "magic"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "invalid_pricing_method"


def test_implied_vol_ok_and_failure():
    ok = client.post("/implied-vol", json={"contract": CONTRACT, "target_price": 10.4506})
    assert ok.status_code == 200
    assert ok.json()["implied_volatility"] == pytest.approx(0.2, abs=1e-3)
    bad = client.post("/implied-vol", json={"contract": CONTRACT, "target_price": 500.0})
    assert bad.status_code == 400
    assert bad.json()["detail"]["reason"] == "price_above_no_arbitrage_bound"


def test_surface_query_out_of_range():
    payload = {
        "maturities": [0.5, 1.0],
        "strikes": [90.0, 100.0],
        "values": [[0.2, 0.18], [0.25, 0.21]],
        "maturity": 5.0,
        "strike": 95.0,
    }
    resp = client.post("/surface/query", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "surface_out_of_range"


def test_scenario_partial_failure():
    contracts = {
        "good": CONTRACT,
        "fragile": dict(CONTRACT, volatility=0.01),
    }
    resp = client.post(
        "/scenario",
        json={"contracts": contracts, "shocks": [{"vol_shift": -0.05}]},
    )
    assert resp.status_code == 200
    sc = resp.json()["scenarios"][0]
    assert [r["contract_id"] for r in sc["results"]] == ["good"]
    assert sc["errors"][0]["reason"] == "negative_volatility"
