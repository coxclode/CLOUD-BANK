"""Tests unitarios: Value Object Money."""

import pytest
from src.domain.value_objects.money import Money


class TestMoneyCreation:

    def test_rounds_to_two_decimals(self):
        m = Money(10.555, "USD")
        assert m.amount == 10.56  # redondeo ROUND_HALF_UP

    def test_rejects_unsupported_currency(self):
        with pytest.raises(ValueError, match="Moneda no soportada"):
            Money(100.0, "XYZ")

    def test_equality_by_value(self):
        assert Money(100.0, "USD") == Money(100.0, "USD")

    def test_inequality_by_currency(self):
        assert Money(100.0, "USD") != Money(100.0, "EUR")


class TestMoneyArithmetic:

    def test_add_same_currency(self):
        result = Money(100.0, "USD").add(Money(50.0, "USD"))
        assert result == Money(150.0, "USD")

    def test_subtract_same_currency(self):
        result = Money(200.0, "USD").subtract(Money(75.0, "USD"))
        assert result == Money(125.0, "USD")

    def test_multiply(self):
        result = Money(100.0, "USD").multiply(1.12)
        assert result.amount == pytest.approx(112.0, abs=0.01)

    def test_divide(self):
        result = Money(300.0, "USD").divide(3.0)
        assert result.amount == pytest.approx(100.0, abs=0.01)

    def test_divide_by_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            Money(100.0, "USD").divide(0)

    def test_add_different_currencies_raises(self):
        with pytest.raises(ValueError, match="divisas distintas"):
            Money(100.0, "USD").add(Money(100.0, "EUR"))


class TestMoneyComparison:

    def test_exceeds_returns_true_when_greater(self):
        assert Money(200.0, "USD").exceeds(Money(100.0, "USD"))

    def test_exceeds_returns_false_when_less(self):
        assert not Money(50.0, "USD").exceeds(Money(100.0, "USD"))

    def test_ordering(self):
        low  = Money(50.0, "USD")
        high = Money(150.0, "USD")
        assert low < high
        assert high > low
        assert low <= low
        assert high >= high
