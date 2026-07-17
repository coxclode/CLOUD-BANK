"""
Servicio de dominio: CreditPolicyService

Encapsula las reglas de política crediticia del banco que NO pertenecen
a ninguna entidad en particular sino al dominio completo.

Estas reglas son INMUTABLES desde la perspectiva del sistema: están codificadas
como política del banco (Basel III, política interna, regulación local).
No son configurables en runtime — cualquier cambio requiere revisión regulatoria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.entities.credit_application import CreditApplication, CreditPurpose
from src.domain.entities.credit_decision import CreditTerms
from src.domain.value_objects.applicant import EmploymentType
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore, RiskBand


@dataclass(frozen=True)
class PolicyViolation:
    rule_code: str
    description: str
    is_blocking: bool
    rule_category: str


@dataclass
class PolicyEvaluation:
    is_approved: bool
    violations: list[PolicyViolation]
    max_approvable_amount: Optional[Money]
    interest_rate_floor: float
    requires_guarantor: bool
    requires_collateral: bool

    @property
    def blocking_violations(self) -> list[PolicyViolation]:
        return [v for v in self.violations if v.is_blocking]

    @property
    def warning_violations(self) -> list[PolicyViolation]:
        return [v for v in self.violations if not v.is_blocking]

    @property
    def rejection_reasons(self) -> list[str]:
        return [v.description for v in self.blocking_violations]


class CreditPolicyService:
    """
    Aplica la política crediticia del banco como servicio de dominio.

    Reglas incorporadas (Basel III + Política Interna):
      HARD-1: Fraud score ≥ 0.85 → Bloqueo absoluto
      HARD-2: AML positivo → Bloqueo absoluto
      HARD-3: DTI post-crédito > 50% → Bloqueo
      HARD-4: PD (12m) > 70% → Bloqueo
      HARD-5: Solicitudes activas > 2 → Bloqueo
      HARD-6: Monto > 500.000 → Bloqueo (límite del sistema)
      SOFT-1: Monto > 50.000 → Requiere escalación comité
      SOFT-2: PD ∈ (45%, 70%] → Borderline, requiere escalación
      SOFT-3: Banda E/F → Requiere garantía
      SOFT-4: Empleo < 6 meses → Requiere garante
    """

    # ── Umbrales de política (NO modificar sin aprobación regulatoria) ────────
    FRAUD_BLOCK_THRESHOLD      = 0.85
    DTI_BLOCK_THRESHOLD        = 0.50
    PD_BLOCK_THRESHOLD         = 0.70
    PD_ESCALATION_THRESHOLD    = 0.45
    MAX_ACTIVE_APPLICATIONS    = 2
    HIGH_AMOUNT_ESCALATION     = 50_000.0
    ABSOLUTE_MAX_AMOUNT        = 500_000.0
    MIN_EMPLOYMENT_MONTHS      = 6.0
    INTEREST_RATE_FLOOR_AA     = 8.0
    INTEREST_RATE_FLOOR_A      = 10.0
    INTEREST_RATE_FLOOR_B      = 13.0
    INTEREST_RATE_FLOOR_C      = 17.0
    INTEREST_RATE_FLOOR_D      = 22.0
    INTEREST_RATE_FLOOR_E      = 28.0
    INTEREST_RATE_FLOOR_F      = 35.0

    def evaluate_application(
        self,
        application: CreditApplication,
        risk_score: RiskScore,
        fraud_score: float,
        aml_clear: bool,
        post_credit_dti: float,
        active_applications_count: int,
    ) -> PolicyEvaluation:
        """
        Evalúa si la solicitud cumple la política crediticia.
        Devuelve una evaluación con todas las violaciones (bloqueantes y advertencias).
        """
        violations: list[PolicyViolation] = []
        is_approved = True

        # ── REGLAS DURAS (bloqueantes) ─────────────────────────────────────────
        if fraud_score >= self.FRAUD_BLOCK_THRESHOLD:
            violations.append(PolicyViolation(
                rule_code="HARD-1",
                description=f"Fraud score {fraud_score:.4f} supera el umbral crítico {self.FRAUD_BLOCK_THRESHOLD}.",
                is_blocking=True,
                rule_category="FRAUD",
            ))
            is_approved = False

        if not aml_clear:
            violations.append(PolicyViolation(
                rule_code="HARD-2",
                description="El solicitante tiene alertas AML (Anti Money Laundering) activas.",
                is_blocking=True,
                rule_category="COMPLIANCE",
            ))
            is_approved = False

        if post_credit_dti > self.DTI_BLOCK_THRESHOLD:
            violations.append(PolicyViolation(
                rule_code="HARD-3",
                description=(
                    f"El DTI post-crédito ({post_credit_dti:.1%}) supera el límite "
                    f"de política ({self.DTI_BLOCK_THRESHOLD:.0%}). "
                    "Capacidad de pago insuficiente."
                ),
                is_blocking=True,
                rule_category="CAPACITY",
            ))
            is_approved = False

        if risk_score.default_probability > self.PD_BLOCK_THRESHOLD:
            violations.append(PolicyViolation(
                rule_code="HARD-4",
                description=(
                    f"Probabilidad de incumplimiento ({risk_score.default_probability:.1%}) "
                    f"supera el umbral máximo ({self.PD_BLOCK_THRESHOLD:.0%})."
                ),
                is_blocking=True,
                rule_category="RISK",
            ))
            is_approved = False

        if active_applications_count > self.MAX_ACTIVE_APPLICATIONS:
            violations.append(PolicyViolation(
                rule_code="HARD-5",
                description=(
                    f"El solicitante tiene {active_applications_count} solicitudes activas "
                    f"(máximo permitido: {self.MAX_ACTIVE_APPLICATIONS})."
                ),
                is_blocking=True,
                rule_category="POLICY",
            ))
            is_approved = False

        if application.requested_amount.amount > self.ABSOLUTE_MAX_AMOUNT:
            violations.append(PolicyViolation(
                rule_code="HARD-6",
                description=(
                    f"El monto solicitado ({application.requested_amount}) "
                    f"supera el límite absoluto del sistema ({self.ABSOLUTE_MAX_AMOUNT:,.0f})."
                ),
                is_blocking=True,
                rule_category="LIMITS",
            ))
            is_approved = False

        # ── REGLAS BLANDAS (advertencias / condiciones) ────────────────────────
        requires_guarantor  = False
        requires_collateral = False

        if (
            application.requested_amount.amount > self.HIGH_AMOUNT_ESCALATION
            and is_approved
        ):
            violations.append(PolicyViolation(
                rule_code="SOFT-1",
                description=(
                    f"Monto {application.requested_amount} requiere revisión del comité de crédito "
                    f"(umbral: {self.HIGH_AMOUNT_ESCALATION:,.0f})."
                ),
                is_blocking=False,
                rule_category="ESCALATION",
            ))

        if (
            self.PD_ESCALATION_THRESHOLD < risk_score.default_probability <= self.PD_BLOCK_THRESHOLD
            and is_approved
        ):
            violations.append(PolicyViolation(
                rule_code="SOFT-2",
                description=(
                    f"PD={risk_score.default_probability:.1%} en zona borderline "
                    f"[{self.PD_ESCALATION_THRESHOLD:.0%}, {self.PD_BLOCK_THRESHOLD:.0%}]. "
                    "Requiere análisis del comité de riesgo."
                ),
                is_blocking=False,
                rule_category="RISK",
            ))

        if risk_score.band in (RiskBand.E, RiskBand.F):
            requires_guarantor  = True
            requires_collateral = True
            violations.append(PolicyViolation(
                rule_code="SOFT-3",
                description=f"Banda de riesgo {risk_score.band.value} requiere garantía y garante.",
                is_blocking=False,
                rule_category="COLLATERAL",
            ))

        if application.applicant.years_of_employment * 12 < self.MIN_EMPLOYMENT_MONTHS:
            requires_guarantor = True
            violations.append(PolicyViolation(
                rule_code="SOFT-4",
                description=(
                    f"Antigüedad laboral ({application.applicant.years_of_employment:.1f} años) "
                    f"inferior a {self.MIN_EMPLOYMENT_MONTHS:.0f} meses. Requiere garante."
                ),
                is_blocking=False,
                rule_category="EMPLOYMENT",
            ))

        # ── Monto máximo aprobable ─────────────────────────────────────────────
        max_approvable = self._compute_max_approvable_amount(
            application=application,
            risk_score=risk_score,
            post_credit_dti=post_credit_dti,
        ) if is_approved else None

        return PolicyEvaluation(
            is_approved=is_approved,
            violations=violations,
            max_approvable_amount=max_approvable,
            interest_rate_floor=self._get_rate_floor(risk_score.band),
            requires_guarantor=requires_guarantor,
            requires_collateral=requires_collateral,
        )

    def compute_credit_terms(
        self,
        application: CreditApplication,
        risk_score: RiskScore,
        suggested_rate: float,
        policy: PolicyEvaluation,
    ) -> CreditTerms:
        """
        Calcula los términos finales del crédito respetando la política.
        La tasa nunca puede ser inferior al floor de política para la banda de riesgo.
        """
        final_rate = max(suggested_rate, policy.interest_rate_floor)
        approved_amount = application.requested_amount

        if policy.max_approvable_amount and approved_amount > policy.max_approvable_amount:
            approved_amount = policy.max_approvable_amount

        return CreditTerms.compute(
            approved_amount=approved_amount,
            annual_rate=final_rate,
            term_months=application.term_months,
            grace_period_days=30 if risk_score.band in (RiskBand.C, RiskBand.D) else 0,
            early_payment_penalty=False,
        )

    def _compute_max_approvable_amount(
        self,
        application: CreditApplication,
        risk_score: RiskScore,
        post_credit_dti: float,
    ) -> Optional[Money]:
        """
        Límite por capacidad de pago y política de riesgo.
        """
        # No queremos superar el monto solicitado
        requested = application.requested_amount.amount

        # Factor de reducción por riesgo (band AA=100%, A=95%, B=90%, C=80%, D=70%, E=50%, F=30%)
        band_factor = {
            RiskBand.AA: 1.00, RiskBand.A: 0.95, RiskBand.B: 0.90,
            RiskBand.C: 0.80,  RiskBand.D: 0.70, RiskBand.E: 0.50,
            RiskBand.F: 0.30,
        }.get(risk_score.band, 0.50)

        max_by_risk = min(requested, self.HIGH_AMOUNT_ESCALATION) * band_factor

        return Money(
            amount=round(min(max_by_risk, requested), 2),
            currency=application.requested_amount.currency,
        )

    def _get_rate_floor(self, band: RiskBand) -> float:
        return {
            RiskBand.AA: self.INTEREST_RATE_FLOOR_AA,
            RiskBand.A:  self.INTEREST_RATE_FLOOR_A,
            RiskBand.B:  self.INTEREST_RATE_FLOOR_B,
            RiskBand.C:  self.INTEREST_RATE_FLOOR_C,
            RiskBand.D:  self.INTEREST_RATE_FLOOR_D,
            RiskBand.E:  self.INTEREST_RATE_FLOOR_E,
            RiskBand.F:  self.INTEREST_RATE_FLOOR_F,
        }[band]
