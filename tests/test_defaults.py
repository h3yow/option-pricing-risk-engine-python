"""默认配置可用性：常见参数范围 cross_check 整体成功；仅显式收紧容差才报分歧。"""
import itertools

import pytest

from app.config import Settings, load_settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.models import Contract, OptionType, PricingMethod


def _grid():
    types = (OptionType.CALL, OptionType.PUT)
    # 深度虚值 K=200、深度实值 K=50；短到期 1 天、长到期 3 年
    strikes = (50.0, 70.0, 90.0, 100.0, 110.0, 150.0, 200.0)
    maturities = (1 / 365, 0.02, 0.05, 0.25, 0.5, 1.0, 2.0, 3.0)
    vols = (0.05, 0.1, 0.2, 0.4, 0.8)
    rates = (0.0, 0.05)
    for ot, k, t, v, r in itertools.product(types, strikes, maturities,
                                            vols, rates):
        yield Contract(contract_id=f"{ot.value}-{k}-{t}-{v}-{r}",
                       option_type=ot, spot=100.0, strike=k,
                       time_to_maturity=t, volatility=v, risk_free_rate=r)


def test_defaults_cover_common_parameter_range_entirely_successful():
    settings = load_settings({})
    engine = PricingEngine(settings)
    contracts = list(_grid())
    assert len(contracts) > 1000  # 确认覆盖广度
    failures = []
    for c in contracts:
        try:
            res = engine.price_one(c, PricingMethod.CROSS_CHECK)
            assert res.method == "cross_check"
            assert res.price >= 0.0
        except PricingError as e:
            failures.append((c.contract_id, e.code, e.detail))
    assert failures == [], (
        f"默认设置下 {len(failures)}/{len(contracts)} 笔正常合约被误判失败，"
        f"示例: {failures[:5]}")


def test_default_batch_request_across_range_all_ok():
    # 默认批量入口同样整体成功（批量上限 256 以下的代表性子集）
    settings = load_settings({})
    engine = PricingEngine(settings)
    sample = list(itertools.islice(_grid(), 256))
    resp = engine.price_batch(sample, PricingMethod.CROSS_CHECK)
    assert resp.failed == 0 and resp.succeeded == len(sample), [
        r.error for r in resp.results if not r.ok][:3]


def test_tight_tolerance_returns_divergence_with_full_evidence():
    # 仅当显式收紧到无法满足时才分歧；且必须给出双方数值/容差/判定依据
    settings = Settings(cross_check_tolerance=1e-9)
    engine = PricingEngine(settings)
    c = Contract(contract_id="atm", option_type=OptionType.CALL, spot=100.0,
                 strike=100.0, time_to_maturity=1.0, volatility=0.2,
                 risk_free_rate=0.05)
    with pytest.raises(PricingError) as exc:
        engine.price_one(c, PricingMethod.CROSS_CHECK)
    err = exc.value
    assert err.code is ErrorCode.METHOD_DIVERGENCE
    detail = err.detail
    assert {"analytic", "monte_carlo", "rel_diff", "tolerance"} <= set(detail)
    assert detail["tolerance"] == 1e-9
    assert detail["rel_diff"] == pytest.approx(
        abs(detail["analytic"] - detail["monte_carlo"])
        / max(abs(detail["analytic"]), 1.0))
    assert detail["rel_diff"] > 1e-9  # 判定依据：相对差确实超过所收紧容差
