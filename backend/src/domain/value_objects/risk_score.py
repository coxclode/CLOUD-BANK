"""
Value Object: RiskScore
Encapsula la puntuación de riesgo compuesta con sus dimensiones.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskBand(str, Enum):
    AA = "AA"   # 850-1000: Riesgo mínimo
    A  = "A"    # 750-849:  Riesgo bajo
    B  = "B"    # 650-749:  Riesgo aceptable
    C  = "C"    # 550-649:  Riesgo moderado
    D  = "D"    # 450-549:  Riesgo elevado
    E  = "E"    # 350-449:  Riesgo alto
    F  = "F"    # 0-349:    Riesgo crítico

    @property
    def is_investment_grade(self) -> bool:
        return self in (self.AA, self.A, self.B, self.C)

    @property
    def color(self) -> str:
        return {
            self.AA: "green", self.A: "green",
            self.B: "yellow", self.C: "yellow",
            self.D: "orange", self.E: "red", self.F: "red",
        }[self]


@dataclass(frozen=True)
class RiskScore:
    """
    Puntuación compuesta de riesgo en escala 0-1000.

    value              : Score final ponderado (0-1000)
    fraud_component    : Aporte del análisis antifraude (0.0-1.0)
    credit_component   : Aporte del historial crediticio (0.0-1.0)
    actuarial_component: Aporte del modelo actuarial (0.0-1.0)
    """

    value: float
    fraud_component: float
    credit_component: float
    actuarial_component: float
    default_probability: float

    # Thresholds de política (Basel III / Política Interna)
    max_acceptable: float = 0.65

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 1000:
            raise ValueError(f"RiskScore.value debe estar entre 0 y 1000: {self.value}")
        for name, val in [
            ("fraud_component", self.fraud_component),
            ("credit_component", self.credit_component),
            ("actuarial_component", self.actuarial_component),
            ("default_probability", self.default_probability),
        ]:
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{name} debe estar entre 0.0 y 1.0: {val}")

    @classmethod
    def compose(
        cls,
        fraud_score: float,
        credit_score: float,
        actuarial_score: float,
        default_probability: float,
        *,
        fraud_weight: float = 0.30,
        credit_weight: float = 0.35,
        actuarial_weight: float = 0.35,
    ) -> "RiskScore":
        """
        Compone un RiskScore a partir de los componentes individuales.
        Los pesos deben sumar 1.0.
        """
        if abs(fraud_weight + credit_weight + actuarial_weight - 1.0) > 0.001:
            raise ValueError("Los pesos de los componentes deben sumar 1.0")

        # Invertir: 0 = sin riesgo → score alto (900-1000), 1 = máximo riesgo → score bajo (0-100)
        composite_risk = (
            fraud_score * fraud_weight
            + credit_score * credit_weight
            + actuarial_score * actuarial_weight
        )
        value = max(0.0, min(1000.0, (1.0 - composite_risk) * 1000))

        return cls(
            value=round(value, 2),
            fraud_component=fraud_score,
            credit_component=credit_score,
            actuarial_component=actuarial_score,
            default_probability=default_probability,
        )

    @property
    def band(self) -> RiskBand:
        v = self.value
        if v >= 850: return RiskBand.AA
        if v >= 750: return RiskBand.A
        if v >= 650: return RiskBand.B
        if v >= 550: return RiskBand.C
        if v >= 450: return RiskBand.D
        if v >= 350: return RiskBand.E
        return RiskBand.F

    @property
    def is_unacceptable(self) -> bool:
        return self.default_probability > self.max_acceptable

    @property
    def normalized(self) -> float:
        """Score normalizado 0.0-1.0 (1.0 = riesgo mínimo)."""
        return self.value / 1000.0

    @property
    def expected_loss_factor(self) -> float:
        """Factor de pérdida esperada EL = PD × LGD × EAD (LGD asumido 0.45 por defecto)."""
        lgd = 0.45
        return self.default_probability * lgd

    def __str__(self) -> str:
        return f"RiskScore({self.value:.1f}/{self.band.value}, PD={self.default_probability:.4f})"
