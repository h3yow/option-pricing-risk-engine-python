import pytest

from app.config import load_settings
from app.engine import PricingEngine
from app.models import Contract, OptionType


@pytest.fixture()
def settings():
    return load_settings({})


@pytest.fixture()
def engine(settings):
    return PricingEngine(settings)


def make_contract(**overrides) -> Contract:
    base = dict(contract_id="c1", option_type=OptionType.CALL, spot=100.0,
                strike=100.0, time_to_maturity=1.0, volatility=0.2,
                risk_free_rate=0.05, dividend_yield=0.0, quantity=1.0)
    base.update(overrides)
    return Contract(**base)


@pytest.fixture()
def contract():
    return make_contract()
