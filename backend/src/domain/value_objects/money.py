"""
Value Object: Money
Inmutable. Dos instancias con mismo monto y divisa son iguales.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


SUPPORTED_CURRENCIES = {"USD", "EUR", "PEN", "COP", "MXN", "CLP", "ARS", "BRL"}


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"Moneda no soportada: '{self.currency}'. "
                f"Monedas válidas: {SUPPORTED_CURRENCIES}"
            )
        # Garantizar redondeo bancario a 2 decimales
        object.__setattr__(
            self,
            "amount",
            float(Decimal(str(self.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        )

    def add(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def multiply(self, factor: float) -> "Money":
        return Money(amount=self.amount * factor, currency=self.currency)

    def divide(self, divisor: float) -> "Money":
        if divisor == 0:
            raise ZeroDivisionError("No se puede dividir dinero entre cero.")
        return Money(amount=self.amount / divisor, currency=self.currency)

    def is_positive(self) -> bool:
        return self.amount > 0

    def is_zero(self) -> bool:
        return self.amount == 0.0

    def exceeds(self, limit: "Money") -> bool:
        self._require_same_currency(limit)
        return self.amount > limit.amount

    def _require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Operación entre divisas distintas: {self.currency} ≠ {other.currency}"
            )

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money(amount={self.amount}, currency='{self.currency}')"

    def __lt__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount >= other.amount
