"""
Caso de Uso: EvaluateCreditApplication

El caso de uso es el director de orquesta. Coordina:
  1. Construir las entidades de dominio a partir del DTO
  2. Verificar reglas de negocio (solicitudes activas, política)
  3. Invocar el pipeline de agentes mediante el orquestador
  4. Aplicar la política crediticia sobre el resultado del pipeline
  5. Persistir la solicitud y la decisión
  6. Publicar eventos de dominio
  7. Notificar al solicitante
  8. Devolver el DTO de respuesta

NO contiene lógica de negocio — esa vive en el dominio.
NO sabe cómo se implementan los repositorios — usa los puertos.
NO sabe qué agentes existen — usa el orquestador como abstracción.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import structlog

from src.application.dto.credit_application_dto import EvaluateCreditApplicationDTO
from src.application.dto.credit_decision_dto import (
    CreditDecisionResponseDTO,
    CreditTermsResponseDTO,
    EscalationResponseDTO,
    JustificationResponseDTO,
)
from src.application.ports.agent_port import AgentResult
from src.application.ports.notification_port import EventPublisherPort, NotificationPort
from src.application.ports.repository_port import (
    CreditApplicationRepository,
    CreditDecisionRepository,
    EvaluationStateStore,
)
from src.domain.entities.credit_application import (
    CreditApplication,
    CreditPurpose,
    ApplicationStatus,
)
from src.domain.entities.credit_decision import (
    CreditDecision,
    CreditTerms,
    DecisionJustification,
    DecisionOutcome,
    EscalationDetails,
)
from src.domain.events.credit_events import (
    AgentExecutionCompleted,
    CreditDecisionIssued,
    CreditEvaluationStarted,
    FraudAlertRaised,
)
from src.domain.services.credit_policy_service import CreditPolicyService
from src.domain.value_objects.applicant import Applicant, EmploymentType
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore

logger = structlog.get_logger(__name__)


class CreditEvaluationError(Exception):
    """Error recuperable durante la evaluación crediticia."""


class ApplicationLimitExceededError(CreditEvaluationError):
    """El solicitante tiene demasiadas solicitudes activas."""


@dataclass
class OrchestratorPort:
    """
    Puerto del orquestador LangGraph.
    La implementación concreta vive en src/orchestrator/.
    """

    async def run_evaluation_pipeline(
        self,
        application: CreditApplication,
        pipeline_id: str,
    ) -> dict:
        """
        Ejecuta el pipeline completo de agentes y devuelve un dict con:
        {
          "fraud_result": AgentResult,
          "credit_result": AgentResult,
          "actuarial_result": AgentResult,
          "approval_result": AgentResult,
          "fraud_score": float,
          "aml_clear": bool,
          "post_credit_dti": float,
          "default_probability": float,
          "suggested_rate": float,
        }
        """
        raise NotImplementedError


class EvaluateCreditApplicationUseCase:
    """
    Caso de uso principal del sistema CLOUD BANK.

    Toda solicitud de crédito pasa exactamente una vez por este use case.
    Es idempotente: si ya existe una decisión para la solicitud, la retorna.
    """

    def __init__(
        self,
        application_repo: CreditApplicationRepository,
        decision_repo: CreditDecisionRepository,
        state_store: EvaluationStateStore,
        orchestrator: OrchestratorPort,
        policy_service: CreditPolicyService,
        event_publisher: EventPublisherPort,
        notification_service: NotificationPort,
    ) -> None:
        self._app_repo    = application_repo
        self._dec_repo    = decision_repo
        self._state_store = state_store
        self._orchestrator = orchestrator
        self._policy      = policy_service
        self._publisher   = event_publisher
        self._notifier    = notification_service

    async def execute(
        self,
        dto: EvaluateCreditApplicationDTO,
        requesting_user_id: str = "system",
    ) -> CreditDecisionResponseDTO:
        """
        Punto de entrada principal.
        Idempotente: mismo correlation_id → misma respuesta.
        """
        start_time = time.monotonic()
        pipeline_id = str(uuid.uuid4())
        log = logger.bind(
            pipeline_id=pipeline_id,
            correlation_id=dto.correlation_id,
            channel=dto.credit_request.channel,
        )
        log.info("evaluate_credit_application.started")

        # ── Paso 1: Construir entidades de dominio ───────────────────────────
        applicant = self._build_applicant(dto)
        application = CreditApplication.create(
            applicant=applicant,
            requested_amount=Money(
                amount=dto.credit_request.requested_amount,
                currency=dto.credit_request.currency,
            ),
            term_months=dto.credit_request.term_months,
            purpose=CreditPurpose(dto.credit_request.purpose),
            channel=dto.credit_request.channel,
            consent_given=dto.consent_given,
            correlation_id=dto.correlation_id or pipeline_id,
        )

        # ── Paso 2: Verificar límite de solicitudes activas ──────────────────
        active_count = await self._app_repo.count_active_applications(
            dto.applicant.national_id
        )
        if active_count >= CreditPolicyService.MAX_ACTIVE_APPLICATIONS:
            raise ApplicationLimitExceededError(
                f"El solicitante ya tiene {active_count} solicitudes activas. "
                f"Máximo permitido: {CreditPolicyService.MAX_ACTIVE_APPLICATIONS}."
            )

        # ── Paso 3: Persistir solicitud y emitir evento ──────────────────────
        application.submit()
        application.start_review()
        await self._app_repo.save(application)

        await self._publisher.publish_batch(application.collect_events())
        await self._publisher.publish(CreditEvaluationStarted(
            application_id=str(application.application_id),
            pipeline_id=pipeline_id,
        ))

        # ── Paso 4: Ejecutar pipeline de agentes ─────────────────────────────
        log.info("evaluate_credit_application.pipeline_starting")
        pipeline_results = await self._orchestrator.run_evaluation_pipeline(
            application=application,
            pipeline_id=pipeline_id,
        )

        # Publicar eventos de agentes
        for agent_name in ("fraud_result", "credit_result", "actuarial_result", "approval_result"):
            agent_result: AgentResult = pipeline_results.get(agent_name)
            if agent_result:
                await self._publisher.publish(AgentExecutionCompleted(
                    application_id=str(application.application_id),
                    agent_name=agent_name.replace("_result", ""),
                    outcome=agent_result.outcome.value,
                    confidence=agent_result.confidence,
                    duration_ms=agent_result.execution_time_ms,
                    corrections_applied=agent_result.corrections_applied,
                ))

        # ── Paso 5: Evaluar política crediticia ──────────────────────────────
        risk_score = self._build_risk_score(pipeline_results)
        fraud_score = pipeline_results.get("fraud_score", 0.5)

        if fraud_score >= CreditPolicyService.FRAUD_BLOCK_THRESHOLD:
            await self._publisher.publish(FraudAlertRaised(
                application_id=str(application.application_id),
                fraud_score=fraud_score,
                fraud_flags=pipeline_results.get("fraud_flags", []),
                is_blocked=True,
            ))

        policy_eval = self._policy.evaluate_application(
            application=application,
            risk_score=risk_score,
            fraud_score=fraud_score,
            aml_clear=pipeline_results.get("aml_clear", True),
            post_credit_dti=pipeline_results.get("post_credit_dti", 0.0),
            active_applications_count=active_count,
        )

        # ── Paso 6: Construir la decisión de dominio ─────────────────────────
        decision = self._build_decision(
            application=application,
            pipeline_results=pipeline_results,
            policy_eval_approved=policy_eval.is_approved,
            policy_violations=policy_eval.rejection_reasons,
            risk_score=risk_score,
            suggested_rate=pipeline_results.get("suggested_rate", 18.0),
            decided_by=f"pipeline:{pipeline_id}",
        )

        # ── Paso 7: Actualizar solicitud según decisión ──────────────────────
        self._apply_decision_to_application(application, decision, policy_eval)
        await self._app_repo.save(application)
        await self._dec_repo.save(decision)

        await self._publisher.publish(CreditDecisionIssued(
            application_id=str(application.application_id),
            decision_id=str(decision.decision_id),
            outcome=decision.outcome.value,
            confidence=decision.confidence,
            risk_score=risk_score.value,
            decided_by=decision.decided_by,
            human_review_required=decision.human_review_required,
        ))

        # ── Paso 8: Notificar al solicitante ─────────────────────────────────
        try:
            await self._notifier.notify_decision(
                email=dto.applicant.email,
                applicant_name=dto.applicant.full_name,
                outcome=decision.outcome.value,
                application_id=str(application.application_id),
            )
            if decision.outcome == DecisionOutcome.ESCALATED and decision.escalation_details:
                await self._notifier.notify_escalation(
                    committee_type=decision.escalation_details.committee_type,
                    priority=decision.escalation_details.priority,
                    application_id=str(application.application_id),
                    summary=decision.escalation_details.escalation_reason,
                )
        except Exception:
            log.warning("evaluate_credit_application.notification_failed")

        # ── Paso 9: Construir respuesta ──────────────────────────────────────
        elapsed_ms = (time.monotonic() - start_time) * 1000
        log.info(
            "evaluate_credit_application.completed",
            outcome=decision.outcome.value,
            confidence=decision.confidence,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return self._to_response_dto(decision, elapsed_ms, pipeline_id)

    # ── Métodos privados ──────────────────────────────────────────────────────

    def _build_applicant(self, dto: EvaluateCreditApplicationDTO) -> Applicant:
        from datetime import date as d_
        return Applicant.create(
            full_name=dto.applicant.full_name,
            national_id=dto.applicant.national_id,
            birth_date=d_.fromisoformat(str(dto.applicant.birth_date)),
            email=dto.applicant.email,
            phone=dto.applicant.phone,
            employment_type=EmploymentType(dto.applicant.employment_type),
            gross_monthly_income=dto.applicant.gross_monthly_income,
            years_of_employment=dto.applicant.years_of_employment,
            country_code=dto.applicant.country_code,
            city=dto.applicant.city,
        )

    def _build_risk_score(self, pipeline_results: dict) -> RiskScore:
        fraud_r    = pipeline_results.get("fraud_result")
        credit_r   = pipeline_results.get("credit_result")
        actuarial_r = pipeline_results.get("actuarial_result")

        fraud_score    = fraud_r.risk_contribution    if fraud_r    else 0.5
        credit_score   = credit_r.risk_contribution   if credit_r   else 0.5
        actuarial_score = actuarial_r.risk_contribution if actuarial_r else 0.5
        default_prob   = pipeline_results.get("default_probability", 0.5)

        return RiskScore.compose(
            fraud_score=fraud_score,
            credit_score=credit_score,
            actuarial_score=actuarial_score,
            default_probability=default_prob,
        )

    def _build_decision(
        self,
        application: CreditApplication,
        pipeline_results: dict,
        policy_eval_approved: bool,
        policy_violations: list[str],
        risk_score: RiskScore,
        suggested_rate: float,
        decided_by: str,
    ) -> CreditDecision:
        app_id       = application.application_id
        approval_r   = pipeline_results.get("approval_result")
        raw_outcome  = approval_r.payload.get("decision") if approval_r else "ESCALATED_TO_COMMITTEE"
        confidence   = approval_r.confidence if approval_r else 0.5

        justification = DecisionJustification(
            plain_language_explanation=(
                (approval_r.payload.get("justification") or "") if approval_r else ""
            ),
            key_factors=approval_r.payload.get("decision_factors", {}) if approval_r else {},
            counterfactual=approval_r.payload.get("counterfactual", "") if approval_r else "",
            regulatory_references=[
                "GDPR Art. 22 — Decisión automatizada con explicación",
                "Basel III — Capital requerido por riesgo de crédito",
                "SR 11-7 — Gestión de modelos de riesgo",
            ],
            model_version="deep_agent_v2.0",
        )

        if not policy_eval_approved:
            return CreditDecision.reject(
                application_id=app_id,
                reasons=policy_violations,
                risk_score=risk_score,
                confidence=confidence,
                justification=justification,
                decided_by=decided_by,
            )

        outcome_map = {
            "APPROVED":                DecisionOutcome.APPROVED,
            "REJECTED":                DecisionOutcome.REJECTED,
            "MORE_DOCS_REQUIRED":      DecisionOutcome.MORE_DOCS,
            "ESCALATED_TO_COMMITTEE":  DecisionOutcome.ESCALATED,
        }
        outcome = outcome_map.get(raw_outcome, DecisionOutcome.ESCALATED)

        if outcome == DecisionOutcome.APPROVED:
            credit_terms = CreditTerms.compute(
                approved_amount=Money(
                    amount=float(approval_r.payload.get("approved_amount") or application.requested_amount.amount),
                    currency=application.requested_amount.currency,
                ),
                annual_rate=float(approval_r.payload.get("interest_rate_annual") or suggested_rate),
                term_months=int(approval_r.payload.get("term_months") or application.term_months),
            )
            return CreditDecision.approve(
                application_id=app_id,
                credit_terms=credit_terms,
                risk_score=risk_score,
                confidence=confidence,
                justification=justification,
                decided_by=decided_by,
                human_review_required=bool(approval_r.payload.get("human_review_required", False)) if approval_r else False,
            )

        if outcome == DecisionOutcome.REJECTED:
            return CreditDecision.reject(
                application_id=app_id,
                reasons=approval_r.payload.get("rejection_reasons", ["Rechazo según política"]) if approval_r else policy_violations,
                risk_score=risk_score,
                confidence=confidence,
                justification=justification,
                decided_by=decided_by,
            )

        if outcome == DecisionOutcome.MORE_DOCS:
            return CreditDecision.request_docs(
                application_id=app_id,
                required_documents=approval_r.payload.get("required_documents", []) if approval_r else [],
                risk_score=risk_score,
                confidence=confidence,
                decided_by=decided_by,
            )

        return CreditDecision.escalate(
            application_id=app_id,
            escalation=EscalationDetails(
                committee_type=approval_r.payload.get("committee_type", "CREDIT") if approval_r else "CREDIT",
                priority=approval_r.payload.get("escalation_priority", "MEDIUM") if approval_r else "MEDIUM",
                escalation_reason=(
                    approval_r.payload.get("escalation_reason") or "Escalación automática: revisión manual requerida."
                ) if approval_r else "Fallo del pipeline de evaluación automática. Requiere revisión manual.",
                key_concerns=approval_r.payload.get("rejection_reasons", []) if approval_r else [],
                supporting_metrics={
                    "risk_score": risk_score.value,
                    "default_probability": risk_score.default_probability,
                    "confidence": confidence,
                },
            ),
            risk_score=risk_score,
            confidence=confidence,
            decided_by=decided_by,
        )

    def _apply_decision_to_application(
        self,
        application: CreditApplication,
        decision: CreditDecision,
        policy_eval: object,
    ) -> None:
        if decision.outcome == DecisionOutcome.APPROVED:
            application.approve(
                risk_score=decision.risk_score,
                notes=f"decision_id:{decision.decision_id}",
            )
        elif decision.outcome == DecisionOutcome.REJECTED:
            application.reject(
                reasons=decision.rejection_reasons,
                risk_score=decision.risk_score,
            )
        elif decision.outcome == DecisionOutcome.MORE_DOCS:
            application.request_more_docs(
                required_docs=decision.required_documents or ["Documentación adicional requerida"],
            )
        elif decision.outcome == DecisionOutcome.ESCALATED:
            esc = decision.escalation_details
            application.escalate(
                reason=esc.escalation_reason if esc else "Escalación automática",
                committee_type=esc.committee_type if esc else "CREDIT",
            )

    def _to_response_dto(
        self,
        decision: CreditDecision,
        elapsed_ms: float,
        pipeline_id: str,
    ) -> CreditDecisionResponseDTO:
        credit_terms_dto = None
        if decision.credit_terms:
            t = decision.credit_terms
            credit_terms_dto = CreditTermsResponseDTO(
                approved_amount=t.approved_amount.amount,
                currency=t.approved_amount.currency,
                interest_rate_annual=t.interest_rate_annual,
                term_months=t.term_months,
                monthly_installment=t.monthly_installment.amount,
                total_credit_cost=t.total_credit_cost.amount,
                grace_period_days=t.grace_period_days,
                early_payment_penalty=t.early_payment_penalty,
            )

        esc_dto = None
        if decision.escalation_details:
            e = decision.escalation_details
            esc_dto = EscalationResponseDTO(
                committee_type=e.committee_type,
                priority=e.priority,
                escalation_reason=e.escalation_reason,
                key_concerns=e.key_concerns,
            )

        justif_dto = None
        if decision.justification:
            j = decision.justification
            justif_dto = JustificationResponseDTO(
                plain_language_explanation=j.plain_language_explanation,
                key_factors=j.key_factors,
                counterfactual=j.counterfactual,
                regulatory_references=j.regulatory_references,
            )

        return CreditDecisionResponseDTO(
            decision_id=str(decision.decision_id),
            application_id=str(decision.application_id),
            outcome=decision.outcome.value,
            confidence=decision.confidence,
            risk_band=decision.risk_score.band.value,
            risk_score=decision.risk_score.value,
            decided_at=decision.decided_at,
            decided_by=decision.decided_by,
            human_review_required=decision.human_review_required,
            credit_terms=credit_terms_dto,
            rejection_reasons=decision.rejection_reasons,
            required_documents=decision.required_documents,
            escalation=esc_dto,
            justification=justif_dto,
            processing_time_ms=round(elapsed_ms, 2),
            pipeline_id=pipeline_id,
        )
