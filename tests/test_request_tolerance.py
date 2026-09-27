"""单次请求级一致性容差：默认保持不变、显式收紧报分歧并附完整判定依据、非法值拒绝。

覆盖单笔 /price、批量 /price/batch、情景 /scenarios、组合 /scenarios/portfolio。
"""
import pytest
from fastapi.testclient import TestClient

from app.errors import PricingError
from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


CONTRACT = {"contract_id": "a", "option_type": "call", "spot": 100.0,
            "strike": 100.0, "time_to_maturity": 1.0, "volatility": 0.2,
            "risk_free_rate": 0.05}

EVIDENCE_KEYS = {"analytic", "monte_carlo", "rel_diff", "tolerance",
                 "criterion"}


def test_price_default_tolerance_still_agrees(client):
    # 不传 cross_check_tolerance：默认 2% 容差下成功
    r = client.post("/price", json={"contract": CONTRACT,
                                    "method": "cross_check"})
    assert r.status_code == 200
    assert r.json()["method"] == "cross_check"


def test_price_tight_tolerance_reports_divergence_with_evidence(client):
    r = client.post("/price", json={"contract": CONTRACT,
                                    "method": "cross_check",
                                    "cross_check_tolerance": 1e-9})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "METHOD_DIVERGENCE"
    detail = err["detail"]
    assert EVIDENCE_KEYS <= set(detail), detail
    assert detail["tolerance"] == 1e-9
    assert detail["criterion"] == "rel_diff > tolerance"
    # 判定依据可复核：相对差确实超过所用容差
    assert detail["rel_diff"] == pytest.approx(
        abs(detail["analytic"] - detail["monte_carlo"])
        / max(abs(detail["analytic"]), 1.0))
    assert detail["rel_diff"] > 1e-9


def test_price_tight_tolerance_changes_result_versus_default(client):
    # 关键回归点：收紧容差必须真正参与判定（默认设置下该笔成功）
    default = client.post("/price",
                          json={"contract": CONTRACT, "method": "cross_check"})
    tight = client.post("/price",
                        json={"contract": CONTRACT, "method": "cross_check",
                              "cross_check_tolerance": 1e-9})
    assert default.status_code == 200
    assert tight.status_code == 422


def test_price_loose_tolerance_still_succeeds(client):
    # 显式放宽也生效（与默认结果一致但走的是请求值）
    r = client.post("/price", json={"contract": CONTRACT,
                                    "method": "cross_check",
                                    "cross_check_tolerance": 0.5})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [0.0, -0.01, 5e-10, -1.0])
def test_single_invalid_tolerance_rejected_not_silently_defaulted(
        client, bad):
    r = client.post("/price", json={"contract": CONTRACT,
                                    "method": "cross_check",
                                    "cross_check_tolerance": bad})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION", r.text
    assert err["detail"]["field"] == "cross_check_tolerance"


@pytest.mark.parametrize("bad", ["abc", None])
def test_single_non_numeric_tolerance_rejected(client, bad):
    # "abc" 无法识别为数值，必须明确拒绝；None 即未传，走默认（成功）
    if bad is None:
        r = client.post("/price", json={"contract": CONTRACT,
                                        "method": "cross_check",
                                        "cross_check_tolerance": None})
        assert r.status_code == 200
        return
    r = client.post("/price", json={"contract": CONTRACT,
                                    "method": "cross_check",
                                    "cross_check_tolerance": bad})
    assert r.status_code == 422


def test_batch_tight_tolerance_failed_leg_has_evidence(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 200, r.text  # 逐笔隔离失败，请求本身成功
    body = r.json()
    assert (body["succeeded"], body["failed"]) == (0, 1)
    err = body["results"][0]["error"]
    assert err["code"] == "METHOD_DIVERGENCE"
    assert EVIDENCE_KEYS <= set(err["detail"])
    assert err["detail"]["tolerance"] == 1e-9


def test_batch_default_tolerance_all_ok(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "cross_check"})
    body = r.json()
    assert body["failed"] == 0


def test_batch_invalid_tolerance_rejects_entire_request_no_partials(client):
    # 非法容差在任何逐笔计算之前拒绝：响应为 422，而非逐笔结果
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT, CONTRACT], "method": "cross_check",
        "cross_check_tolerance": 0.0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


def test_scenarios_tight_tolerance_reports_divergence(client):
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": 0.0}], "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 200
    legs = r.json()["shocks"][0]["legs"]
    assert (legs[0]["ok"], legs[0]["error"]["code"]) == (
        False, "METHOD_DIVERGENCE")
    assert EVIDENCE_KEYS <= set(legs[0]["error"]["detail"])


def test_scenarios_default_tolerance_ok(client):
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": 0.01}], "method": "cross_check"})
    assert r.status_code == 200
    assert r.json()["shocks"][0]["failed"] == 0


def test_scenarios_invalid_tolerance_rejected(client):
    r = client.post("/scenarios", json={
        "name": "s", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": 0.0}], "method": "cross_check",
        "cross_check_tolerance": -1.0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


def test_portfolio_tight_tolerance_reports_divergence(client):
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": 0.0}], "method": "cross_check",
        "cross_check_tolerance": 1e-9})
    assert r.status_code == 200
    leg = r.json()["shocks"][0]["legs"][0]
    assert leg["ok"] is False
    assert leg["error"]["code"] == "METHOD_DIVERGENCE"
    assert EVIDENCE_KEYS <= set(leg["error"]["detail"])


def test_portfolio_invalid_tolerance_rejected(client):
    r = client.post("/scenarios/portfolio", json={
        "name": "p", "contracts": [CONTRACT],
        "shocks": [{"spot_pct": 0.0}], "method": "cross_check",
        "cross_check_tolerance": 1e-12})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


def test_nan_inf_tolerance_rejected_at_engine():
    # 绕过 JSON（NaN/Inf 无法合法表达）直接验证引擎层明确拒绝
    from app.config import load_settings
    from app.engine import PricingEngine
    from app.errors import ErrorCode
    from app.models import PricingMethod
    from tests.conftest import make_contract

    engine = PricingEngine(load_settings({}))
    c = make_contract()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(PricingError) as exc:
            engine.price_one(c, PricingMethod.CROSS_CHECK, bad)
        assert exc.value.code is ErrorCode.VALIDATION, bad


def test_unrecognized_precedence_rejected_over_http(client):
    r = client.post("/price/batch", json={
        "contracts": [CONTRACT], "method": "analytic",
        "market_version": "whatever", "precedence": "bogus"})
    # Fastapi 对枚举非法取值返回 422 请求校验错误（明确拒绝，不按默认处理）
    assert r.status_code == 422


@pytest.mark.parametrize("method", ["analytic", "monte_carlo"])
def test_invalid_tolerance_rejected_even_for_single_path_methods(
        client, method):
    # 非法容差不得因走单途径而被静默忽略
    r = client.post("/price", json={"contract": CONTRACT, "method": method,
                                    "cross_check_tolerance": 0.0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


@pytest.mark.parametrize("method", ["analytic", "monte_carlo"])
def test_valid_tolerance_no_side_effect_on_single_path_methods(client, method):
    r = client.post("/price", json={"contract": CONTRACT, "method": method,
                                    "cross_check_tolerance": 0.01})
    assert r.status_code == 200
    assert r.json()["method"] == method
