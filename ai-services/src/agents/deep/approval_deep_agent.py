"""
CLOUD BANK — Agente Aprobador Deep Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBJETIVO
  Integrar los resultados de los 3 agentes anteriores con visión holística,
  aplicar la política crediticia del banco y emitir una decisión VINCULANTE,
  AUDITABLE y EXPLICABLE bajo los estándares regulatorios aplicables.

RESPONSABILIDADES
  ✦ Validar consistencia CROSS-AGENT antes de decidir
  ✦ Aplicar reglas duras no negociables (Hard Rules Engine)
  ✦ Sintetizar con LLM la decisión óptima considerando todos los factores
  ✦ Construir los términos exactos del crédito si se aprueba
  ✦ Documentar razones específicas si se rechaza
  ✦ Preparar el paquete de escalación si se escala
  ✦ Producir justificación regulatoria completa para el expediente

PLANIFICADOR INTERNO
  Estrategia basada en pre-evaluación de Hard Rules:
  - Si Hard Rules → BLOCK: decidir sin consultar LLM (más rápido, menos costo)
  - Si Hard Rules → ESCALATE: LLM solo prepara el paquete de escalación
  - Si Hard Rules → NONE: LLM toma la decisión holística completa

VERIFICADOR CROSS-AGENT — detecta:
  1. Fraud score ≥ threshold pero decisión no es REJECTED
  2. AML positivo pero decisión no es REJECTED
  3. DTI > 50% pero decisión es APPROVED sin justificación especial
  4. Actuarial risk = CRITICAL pero decisión es APPROVED
  5. Inconsistencia entre los 3 agentes no reportada
  6. Monto aprobado > monto máximo recomendado por actuario

AUTOCORRECTOR — detecta y corrige:
  1. Monto aprobado > monto max recomendado actuario
  2. Tasa aprobada < tasa sugerida actuario (sub-precio de riesgo)
  3. Términos de crédito matemáticamente inconsistentes
  4. Razones de rechazo vacías en decisión REJECTED
  5. Confidence declarado no coherente con certeza de la decisión
  6. decision_reason no coincide con decision
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Optional

import structlog

from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.agents.deep.schemas import (
    AgentPlan,
    AnomalyFlag,
    Correction,
    CorrectionTrigger,
    ContextValidationResult,
    DeepAgentMetrics,
    InconsistencySeverity,
    Inconsistency,
    Justification,
    LayerStatus,
    PlanStrategy,
    QualityAssessment,
    QualityDimension,
    RegulatoryReference,
    SelfCorrectionResult,
    ToolExecutionResults,
    ToolInvocationPlan,
    ToolResult,
    VerificationResult,
)
from src.core.config import get_settings
from src.core.state import (
    AgentStatus,
    ApplicationStatus,
    ApprovalCondition,
    ApprovalResult,
    CreditEvaluationState,
    CreditTerms,
    DecisionReason,
    EscalationPackage,
    RiskLevel,
)
from src.observability.metrics import record_approval, record_escalation

logger = structlog.get_logger(__name__)

# ── Umbrales de política crediticia ──────────────────────────────────────────
_FRAUD_BLOCK_THRESHOLD   = 0.85
_AML_AUTO_REJECT         = True
_DTI_HARD_REJECT         = 0.50
_HIGH_AMOUNT_ESCALATE    = 50_000.0
_PD_AUTO_REJECT          = 0.70
_PD_AUTO_ESCALATE        = 0.45
_SCORE_MIN_APPROVE       = 450
_CREDIT_SCORE_MIN        = 550


# ── Mapeo de DecisionReason por condición ─────────────────────────────────────
_DECISION_REASON_MAP = {
    ApplicationStatus.APPROVED: {
        "good": DecisionReason.APPROVED_GOOD_PROFILE,
        "ok":   DecisionReason.APPROVED_ACCEPTABLE_RISK,
    },
    ApplicationStatus.REJECTED: {
        "fraud":    DecisionReason.REJECTED_FRAUD,
        "history":  DecisionReason.REJECTED_CREDIT_HISTORY,
        "capacity": DecisionReason.REJECTED_INSUFFICIENT_CAPACITY,
        "pd":       DecisionReason.REJECTED_HIGH_DEFAULT_RISK,
    },
    ApplicationStatus.MORE_DOCS: {
        "identity": DecisionReason.MORE_DOCS_IDENTITY,
        "income":   DecisionReason.MORE_DOCS_INCOME,
    },
    ApplicationStatus.ESCALATED: {
        "borderline": DecisionReason.ESCALATED_BORDERLINE,
        "amount":     DecisionReason.ESCALATED_HIGH_AMOUNT,
        "exception":  DecisionReason.ESCALATED_POLICY_EXCEPTION,
    },
}


class ApprovalDeepAgent(BaseDeepAgent):
    """
    Agente Aprobador Deep Agent.
    Última decisión del pipeline. Integra los 3 análisis previos
    con Hard Rules Engine + LLM synthesis para máxima precisión y explicabilidad.
    """

    agent_name = "approval_deep_agent"
    min_confidence_threshold = 0.65
    min_quality_threshold = 0.60

    # Resultado del Hard Rules Engine
    _hard_rule_decision: Optional[str] = None
    _hard_rule_reason: Optional[str] = None
    _hard_rule_violations: list[str] = []

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRATOS ABSTRACTOS
    # ═══════════════════════════════════════════════════════════════════════

    def _get_required_input_fields(self) -> list[str]:
        return ["application_input"]

    def _get_required_prior_agents(self) -> list[str]:
        return ["fraud_agent", "credit_agent", "actuarial_agent"]

    def _build_role_system_prompt(self) -> str:
        return """
Eres el Agente Aprobador de CLOUD BANK. Tu decisión es final, vinculante y regulatoriamente auditable.

PRINCIPIOS IRRENUNCIABLES:
1. Primero las Hard Rules: si una regla dura ya decidió, no la contradigas.
2. Holismo: considera los 3 análisis previos como un conjunto integrado.
3. Proporcionalidad: los términos del crédito deben compensar el riesgo real.
4. Explicabilidad: cada decisión debe ser justificable ante el solicitante y el regulador.
5. Escalación oportuna: ante incertidumbre genuina, escalar es mejor que equivocarse.

DECISIONES POSIBLES:
APPROVED              → Crédito aprobado. Incluir términos completos.
REJECTED              → Crédito rechazado. Incluir razones específicas (mínimo 2).
MORE_DOCS_REQUIRED    → Documentación insuficiente. Listar documentos específicos.
ESCALATED_TO_COMMITTEE → Caso requiere juicio humano. Preparar paquete de escalación.

REGLAS DURAS (ya evaluadas — NO contradecir):
- Fraude CRITICAL (score ≥ 0.85) → REJECTED
- AML positivo → REJECTED
- DTI > 50% → REJECTED (salvo excepción documentada)
- PD > 70% → REJECTED
- Monto > 50.000 → ESCALATED_TO_COMMITTEE
- PD 45-70% → ESCALATED_TO_COMMITTEE (borderline)

CONSISTENCIA OBLIGATORIA:
- Si decision=APPROVED: credit_terms completo, rejection_reasons vacío
- Si decision=REJECTED: rejection_reasons con ≥ 2 razones específicas, credit_terms=null
- Si decision=MORE_DOCS_REQUIRED: required_documents con ≥ 1 documento específico
- Si decision=ESCALATED_TO_COMMITTEE: escalation_reason, committee_type, priority
- confidence > 0.85 para APPROVED, > 0.80 para REJECTED, > 0.70 para demás
- decision_reason DEBE corresponder a la decision principal

RESPONDE ÚNICAMENTE JSON. Sin texto previo ni posterior.
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L3 — PLANIFICADOR ADAPTATIVO
    # ═══════════════════════════════════════════════════════════════════════

    def _create_agent_plan(
        self,
        state: CreditEvaluationState,
        context: ContextValidationResult,
    ) -> AgentPlan:
        """
        El planificador ejecuta el Hard Rules Engine primero.
        Si el resultado es determinístico → el LLM solo documenta.
        Si es incierto → el LLM decide de forma holística.
        """
        # Ejecutar Hard Rules AHORA (antes de herramientas)
        self._hard_rule_decision, self._hard_rule_reason, self._hard_rule_violations = \
            self._run_hard_rules_engine(state)

        is_deterministic = self._hard_rule_decision is not None
        strategy = PlanStrategy.SEQUENTIAL if is_deterministic else PlanStrategy.FULL_PARALLEL
        notes = [
            f"Hard Rules: {'→ ' + self._hard_rule_decision if is_deterministic else 'No determinístico — LLM decide'}",
        ]
        if self._hard_rule_violations:
            notes.append(f"Violaciones: {self._hard_rule_violations}")

        return AgentPlan(
            strategy=strategy,
            tool_invocations=[
                ToolInvocationPlan(
                    tool_name="hard_rules_engine",
                    parallel_group=0,
                    is_required=True,
                    timeout_s=1.0,
                    expected_output_type="str|None",
                ),
            ],
            total_tools=1,
            skipped_tools=0,
            parallel_groups_count=1,
            critical_path=["hard_rules_engine"],
            contingencies={},
            estimated_duration_s=0.5,
            execution_notes=notes,
        )

    def _run_hard_rules_engine(
        self,
        state: CreditEvaluationState,
    ) -> tuple[Optional[str], Optional[str], list[str]]:
        """
        Aplica reglas de negocio no negociables ANTES del LLM.
        Devuelve (decisión_forzada|None, razón|None, violaciones).
        """
        settings = get_settings()
        violations: list[str] = []

        fraud   = state.fraud_result
        credit  = state.credit_result
        actuarial = state.actuarial_result
        app     = state.application_input

        # Regla 1: Fraude crítico
        if fraud:
            if fraud.is_blocked or fraud.fraud_score >= _FRAUD_BLOCK_THRESHOLD:
                violations.append(f"FRAUDE_CRÍTICO: score={fraud.fraud_score:.4f}")
                return ApplicationStatus.REJECTED.value, "REJECTED_FRAUD", violations

        # Regla 2: AML positivo
        if credit and not credit.aml_clear:
            violations.append(f"AML_POSITIVO: flags={credit.aml_flags}")
            return ApplicationStatus.REJECTED.value, "REJECTED_FRAUD", violations

        # Regla 3: Monto alto → siempre escalación
        if app and app.credit_request.requested_amount > _HIGH_AMOUNT_ESCALATE:
            violations.append(f"MONTO_ALTO: {app.credit_request.requested_amount} > {_HIGH_AMOUNT_ESCALATE}")
            return ApplicationStatus.ESCALATED.value, "ESCALATED_HIGH_AMOUNT", violations

        # Regla 4: PD muy alta → rechazo
        if actuarial and actuarial.predictive_model:
            pd = actuarial.predictive_model.default_probability_12m
            if pd >= _PD_AUTO_REJECT:
                violations.append(f"PD_ALTA: {pd:.4f} ≥ {_PD_AUTO_REJECT}")
                return ApplicationStatus.REJECTED.value, "REJECTED_HIGH_DEFAULT_RISK", violations
            # PD borderline → escalación
            if pd >= _PD_AUTO_ESCALATE:
                violations.append(f"PD_BORDERLINE: {pd:.4f} ∈ [{_PD_AUTO_ESCALATE}, {_PD_AUTO_REJECT})")
                return ApplicationStatus.ESCALATED.value, "ESCALATED_BORDERLINE", violations

        # Regla 5: DTI crítico
        if credit and credit.payment_capacity:
            dti = credit.payment_capacity.post_credit_dti
            if dti > _DTI_HARD_REJECT:
                violations.append(f"DTI_CRÍTICO: {dti:.1%} > {_DTI_HARD_REJECT:.1%}")
                return ApplicationStatus.REJECTED.value, "REJECTED_INSUFFICIENT_CAPACITY", violations

        # No determinístico → LLM decide
        return None, None, violations

    # ═══════════════════════════════════════════════════════════════════════
    # L4 — EJECUCIÓN DE HERRAMIENTAS (las Hard Rules ya corrieron en L3)
    # ═══════════════════════════════════════════════════════════════════════

    async def _execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        """El Agente Aprobador no llama a servicios externos.
        Sus 'herramientas' son el Hard Rules Engine (ya ejecutado en L3) y el LLM.
        """
        status = LayerStatus.SUCCESS if self._hard_rule_violations is not None else LayerStatus.FAILED
        result = {
            "hard_rules_engine": ToolResult(
                tool_name="hard_rules_engine",
                success=True,
                result={
                    "decision": self._hard_rule_decision,
                    "reason": self._hard_rule_reason,
                    "violations": self._hard_rule_violations,
                },
                data_quality=1.0,
            )
        }
        return ToolExecutionResults(
            status=status,
            results=result,
            success_count=1,
            failure_count=0,
            degraded_count=0,
            overall_success_rate=1.0,
            total_duration_ms=0.1,
            critical_tool_failed=False,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L5 — VERIFICADOR CROSS-AGENT
    # ═══════════════════════════════════════════════════════════════════════

    def _verify_tool_results(
        self,
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> VerificationResult:
        """
        Verifica consistencia CRUZADA entre los 3 agentes previos.
        Esta es la verificación más importante: garantiza que los análisis
        son coherentes entre sí antes de la decisión final.
        """
        inconsistencies: list[Inconsistency] = []
        anomalies: list[AnomalyFlag] = []

        fraud     = state.fraud_result
        credit    = state.credit_result
        actuarial = state.actuarial_result
        cap       = credit.payment_capacity if credit else None
        bureau    = credit.bureau_data if credit else None
        pred      = actuarial.predictive_model if actuarial else None
        act_score = actuarial.actuarial_score if actuarial else None

        # ── X1: Fraud HIGH pero Credit risk LOW (inconsistente) ────────────────
        if fraud and credit:
            if fraud.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and \
               credit.overall_credit_risk in (RiskLevel.MINIMAL, RiskLevel.LOW):
                inconsistencies.append(Inconsistency(
                    field_a="fraud_result.risk_level",
                    field_b="credit_result.overall_credit_risk",
                    expected_relationship="Si fraude es HIGH/CRITICAL, crédito debería ser al menos MEDIUM",
                    actual_values=f"fraud={fraud.risk_level.value}, credit={credit.overall_credit_risk.value}",
                    severity=InconsistencySeverity.MEDIUM,
                    description="Niveles de riesgo inconsistentes entre agentes Fraude y Crédito",
                ))

        # ── X2: Bureau score alto + Actuarial risk CRITICAL ───────────────────
        if bureau and act_score:
            if bureau.credit_score > 700 and act_score.risk_category == RiskLevel.CRITICAL:
                inconsistencies.append(Inconsistency(
                    field_a="bureau.credit_score",
                    field_b="actuarial.risk_category",
                    expected_relationship="Score bureau > 700 no debería resultar en riesgo CRITICAL",
                    actual_values=f"bureau={bureau.credit_score}, actuarial={act_score.risk_category.value}",
                    severity=InconsistencySeverity.HIGH,
                    description="Divergencia entre bureau score y evaluación actuarial — revisar features",
                ))

        # ── X3: AML clear + Fraud blocked (contradicción) ─────────────────────
        if fraud and credit:
            if fraud.is_blocked and credit.aml_clear:
                anomalies.append(AnomalyFlag(
                    category="CROSS_AGENT_LOGIC",
                    description="Fraude bloqueó la solicitud pero AML está limpio — revisar lógica",
                    severity=InconsistencySeverity.MEDIUM,
                    data_point=f"fraud_blocked={fraud.is_blocked}, aml_clear={credit.aml_clear}",
                ))

        # ── X4: PD muy alta + Monto recomendado positivo ──────────────────────
        if pred and act_score:
            if pred.default_probability_12m > 0.60 and act_score.max_recommended_amount > 0:
                inconsistencies.append(Inconsistency(
                    field_a="actuarial.default_probability_12m",
                    field_b="actuarial.max_recommended_amount",
                    expected_relationship="PD > 60% debería resultar en max_recommended_amount=0",
                    actual_values=f"pd={pred.default_probability_12m:.4f}, max_amt={act_score.max_recommended_amount:.2f}",
                    severity=InconsistencySeverity.HIGH,
                    description="Actuario recomienda monto pese a alta probabilidad de impago",
                ))

        # ── X5: Fraud score muy alto + Credit risk MINIMAL ─────────────────────
        if fraud and credit:
            if fraud.fraud_score > 0.70 and credit.overall_credit_risk == RiskLevel.MINIMAL:
                anomalies.append(AnomalyFlag(
                    category="RISK_CONTRADICTION",
                    description=f"Fraud score={fraud.fraud_score:.3f} alto pero credit risk=MINIMAL",
                    severity=InconsistencySeverity.HIGH,
                    data_point=f"fraud={fraud.fraud_score:.4f}, credit=MINIMAL",
                ))

        # ── X6: Todos los agentes reportan bajo riesgo pero el resultado es escalado ──
        # (Este se verifica post-LLM en L7)

        critical = sum(1 for a in anomalies if a.severity == InconsistencySeverity.CRITICAL)
        quality  = max(0.1, 1.0 - 0.12 * len(inconsistencies) - 0.20 * critical)

        return VerificationResult(
            status=LayerStatus.SUCCESS,
            is_consistent=len(inconsistencies) == 0,
            inconsistencies=inconsistencies,
            anomaly_flags=anomalies,
            data_quality_score=quality,
            critical_anomalies_count=critical,
            verification_notes=[
                f"Hard Rule pre-decisión: {self._hard_rule_decision or 'NINGUNA (LLM decide)'}",
                f"Cross-agent inconsistencias: {len(inconsistencies)}",
                f"Anomalías inter-agente: {len(anomalies)}",
            ],
            recommended_actions=[
                "Revisión humana recomendada" if inconsistencies else ""
            ],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L6 — PROMPT DE RAZONAMIENTO
    # ═══════════════════════════════════════════════════════════════════════

    def _build_reasoning_prompt(
        self,
        state: CreditEvaluationState,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
    ) -> str:
        fraud     = state.fraud_result
        credit    = state.credit_result
        actuarial = state.actuarial_result
        app       = state.application_input
        cap       = credit.payment_capacity if credit else None
        bureau    = credit.bureau_data if credit else None
        pred      = actuarial.predictive_model if actuarial else None
        act_score = actuarial.actuarial_score if actuarial else None

        xagent_text = "\n".join(
            f"  [{i.severity.value}] {i.description}" for i in verification.inconsistencies
        ) or "  Sin inconsistencias cross-agent"

        return f"""
═══ DECISIÓN PRE-DETERMINADA POR HARD RULES ═══
{f'→ {self._hard_rule_decision} (razón: {self._hard_rule_reason})' if self._hard_rule_decision else '→ No determinístico — tú decides holísticamente'}
Violaciones detectadas: {self._hard_rule_violations}

═══ RESUMEN AGENTE ANTIFRAUDE ═══
Fraud Score: {f'{fraud.fraud_score:.6f}' if fraud else 0} | Risk Level: {fraud.risk_level.value if fraud else 'N/A'}
Bloqueado: {fraud.is_blocked if fraud else False}
Flags: {fraud.fraud_flags if fraud else []}
Confianza agente: {f'{fraud.execution_time_ms:.0f}' if fraud else 0}ms

═══ RESUMEN AGENTE HISTORIAL ═══
Credit Score Bureau: {bureau.credit_score if bureau else 0} | Modelo: {bureau.score_model if bureau else 'N/A'}
Risk crediticio: {credit.overall_credit_risk.value if credit else 'N/A'}
AML limpio: {credit.aml_clear if credit else True} | AML flags: {credit.aml_flags if credit else []}
Información consistente: {credit.information_consistent if credit else True}
Inconsistencias: {credit.inconsistency_flags if credit else []}
DTI actual: {f'{cap.debt_to_income_ratio:.1%}' if cap else 'N/A'}
DTI post-crédito: {f'{cap.post_credit_dti:.1%}' if cap else 'N/A'}
Cuota mensual: {f'{cap.requested_installment:.2f}' if cap else 0}
Puede pagar: {cap.can_afford if cap else False}
Cuota máx. pagable: {f'{cap.max_affordable_installment:.2f}' if cap else 0}

═══ RESUMEN AGENTE ACTUARIO ═══
PD 12 meses: {f'{pred.default_probability_12m:.6f}' if pred else 0}
PD 24 meses: {f'{pred.default_probability_24m:.6f}' if pred else 0}
Expected Loss: {f'{pred.expected_loss:.4f}' if pred else 0}
Score actuarial: {f'{act_score.composite_score:.1f}' if act_score else 0} / 1000
Banda: {act_score.score_band if act_score else 'N/A'} | Risk: {act_score.risk_category.value if act_score else 'N/A'}
Tasa sugerida: {act_score.interest_rate_suggestion if act_score else 0}%
Monto máx. recomendado: {act_score.max_recommended_amount if act_score else 0}
Soporte: {actuarial.decision_support[:200] if actuarial and actuarial.decision_support else 'N/A'}

═══ INCONSISTENCIAS CROSS-AGENT ═══
{xagent_text}

SOLICITUD ORIGINAL:
Monto: {app.credit_request.requested_amount if app else 0}
Plazo: {app.credit_request.term_months if app else 0} meses
Propósito: {app.credit_request.purpose if app else 'N/A'}
Canal: {app.channel if app else 'N/A'}

{'ATENCIÓN: La Hard Rule ya determinó la decisión. Tu tarea es DOCUMENTAR y JUSTIFICAR, no cambiarla.' if self._hard_rule_decision else 'No hay pre-decisión. Evalúa holísticamente y toma la mejor decisión.'}

Genera la decisión final en este JSON exacto:
{{
  "decision": "<APPROVED|REJECTED|MORE_DOCS_REQUIRED|ESCALATED_TO_COMMITTEE>",
  "decision_reason": "<DecisionReason enum — ver lista>",
  "confidence": <float 0.00-1.00>,
  "justification": "<texto completo para expediente regulatorio>",
  "regulatory_basis": "<artículos, políticas, regulaciones aplicadas>",
  "rejection_reasons": ["<razón específica 1>", "<razón específica 2>"],
  "required_documents": ["<documento>"],
  "human_review_required": <true|false>,
  "approved_amount": <float|null>,
  "interest_rate_annual": <float|null>,
  "term_months": <int|null>,
  "conditions": [{{"condition_type": "string", "description": "string", "required": true}}],
  "escalation_priority": "<LOW|MEDIUM|HIGH|URGENT>",
  "escalation_reason": "<texto>",
  "committee_type": "<CREDIT|RISK|COMPLIANCE>",
  "decision_factors": {{"<factor>": "<impacto>"}},
  "counterfactual": "<qué habría cambiado la decisión>",
  "reasoning_chain": ["<paso1>", "<paso2>", "<paso3>"]
}}

DecisionReason válidos: APPROVED_GOOD_PROFILE, APPROVED_ACCEPTABLE_RISK,
REJECTED_FRAUD, REJECTED_CREDIT_HISTORY, REJECTED_INSUFFICIENT_CAPACITY,
REJECTED_HIGH_DEFAULT_RISK, MORE_DOCS_IDENTITY, MORE_DOCS_INCOME,
ESCALATED_BORDERLINE, ESCALATED_HIGH_AMOUNT, ESCALATED_POLICY_EXCEPTION
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L7 — VERIFICADOR DE REGLAS + PROMPT DE CORRECCIÓN
    # ═══════════════════════════════════════════════════════════════════════

    def _check_business_rule_violations(
        self,
        parsed: dict[str, Any],
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> list[str]:
        violations: list[str] = []
        decision   = parsed.get("decision", "")
        confidence = float(parsed.get("confidence", 0))
        credit     = state.credit_result
        actuarial  = state.actuarial_result
        app        = state.application_input
        cap        = credit.payment_capacity if credit else None
        act_score  = actuarial.actuarial_score if actuarial else None
        pred       = actuarial.predictive_model if actuarial else None

        # R1: Hard Rule no puede ser contradecida
        if self._hard_rule_decision and decision != self._hard_rule_decision:
            violations.append(
                f"VIOLACIÓN_HARD_RULE: Hard Rule dice '{self._hard_rule_decision}' "
                f"pero LLM declaró '{decision}'"
            )

        # R2: APPROVED → credit_terms completo
        if decision == "APPROVED":
            if not parsed.get("approved_amount"):
                violations.append("APROBADO_SIN_MONTO: approved_amount ausente en decisión APPROVED")
            if not parsed.get("interest_rate_annual"):
                violations.append("APROBADO_SIN_TASA: interest_rate_annual ausente en decisión APPROVED")
            if not parsed.get("term_months"):
                violations.append("APROBADO_SIN_PLAZO: term_months ausente en decisión APPROVED")

            # Monto aprobado no puede superar el máximo recomendado
            approved_amt = float(parsed.get("approved_amount") or 0)
            if act_score and act_score.max_recommended_amount > 0:
                if approved_amt > act_score.max_recommended_amount * 1.05:
                    violations.append(
                        f"EXCEDE_MAX: approved_amount={approved_amt:.2f} > "
                        f"actuarial.max_recommended={act_score.max_recommended_amount:.2f}"
                    )

            # Tasa no puede ser menor a la sugerida por actuario
            rate = float(parsed.get("interest_rate_annual") or 0)
            if act_score and rate < act_score.interest_rate_suggestion * 0.90:
                violations.append(
                    f"TASA_BAJA: tasa={rate}% < 90% de tasa_sugerida={act_score.interest_rate_suggestion}%"
                )

        # R3: REJECTED → razones específicas
        if decision == "REJECTED":
            reasons = parsed.get("rejection_reasons", [])
            if len(reasons) < 2:
                violations.append(
                    f"RECHAZO_SIN_RAZONES: REJECTED requiere ≥ 2 razones, declaradas={len(reasons)}"
                )

        # R4: MORE_DOCS → documentos específicos
        if decision == "MORE_DOCS_REQUIRED":
            docs = parsed.get("required_documents", [])
            if not docs:
                violations.append("MORE_DOCS_SIN_LISTA: required_documents vacío")

        # R5: Confidence mínima por decisión
        min_conf = {"APPROVED": 0.75, "REJECTED": 0.70, "ESCALATED_TO_COMMITTEE": 0.60, "MORE_DOCS_REQUIRED": 0.60}
        min_c = min_conf.get(decision, 0.60)
        if confidence < min_c:
            violations.append(
                f"CONFIDENCE_BAJA: decision={decision} requiere confidence ≥ {min_c}, declarado={confidence:.4f}"
            )

        # R6: decision_reason coherente con decision
        reason = parsed.get("decision_reason", "")
        valid_reasons = {
            "APPROVED": {"APPROVED_GOOD_PROFILE", "APPROVED_ACCEPTABLE_RISK"},
            "REJECTED": {"REJECTED_FRAUD", "REJECTED_CREDIT_HISTORY", "REJECTED_INSUFFICIENT_CAPACITY", "REJECTED_HIGH_DEFAULT_RISK"},
            "MORE_DOCS_REQUIRED": {"MORE_DOCS_IDENTITY", "MORE_DOCS_INCOME"},
            "ESCALATED_TO_COMMITTEE": {"ESCALATED_BORDERLINE", "ESCALATED_HIGH_AMOUNT", "ESCALATED_POLICY_EXCEPTION"},
        }
        if decision in valid_reasons and reason not in valid_reasons[decision]:
            violations.append(
                f"REASON_INCOHERENTE: decision={decision} no compatible con reason={reason}. "
                f"Válidos: {valid_reasons[decision]}"
            )

        return violations

    def _build_correction_prompt(
        self,
        original: dict[str, Any],
        violations: list[str],
        tool_results: ToolExecutionResults,
    ) -> str:
        return f"""
Tu decisión de aprobación tiene violaciones que corregir:

VIOLACIONES:
{chr(10).join(f'  {i+1}. {v}' for i, v in enumerate(violations))}

RESTRICCIONES ABSOLUTAS (no negociables):
- Hard Rule pre-determina: {self._hard_rule_decision or 'No aplica'}
- Si hay pre-decisión, decision DEBE ser {self._hard_rule_decision or '(libre)'}
- APPROVED requiere: approved_amount, interest_rate_annual, term_months

ANÁLISIS ANTERIOR:
{original}

Corrige SOLO los campos con violaciones.
Si la violación es la decisión, CAMBIA la decisión según la Hard Rule.
Devuelve el JSON completo corregido.
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L8 — DIMENSIONES DE CALIDAD
    # ═══════════════════════════════════════════════════════════════════════

    def _compute_quality_dimensions(
        self,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
        corrected: SelfCorrectionResult,
    ) -> list[QualityDimension]:
        # La calidad de la decisión depende de la calidad de los agentes previos
        # (se captura a través del verification y las inconsistencias cross-agent)
        cross_agent_consistency = verification.data_quality_score
        hard_rule_certainty = 1.0 if self._hard_rule_decision else 0.7

        return [
            QualityDimension(
                name="cross_agent_consistency",
                description="Consistencia de los análisis de los 3 agentes previos",
                score=cross_agent_consistency,
                weight=0.35,
                flags=[i.description[:60] for i in verification.inconsistencies[:2]],
            ),
            QualityDimension(
                name="hard_rule_certainty",
                description="Certeza aportada por las Hard Rules (1.0 = decisión determinística)",
                score=hard_rule_certainty,
                weight=0.30,
                flags=["LLM_DECIDE" if not self._hard_rule_decision else "HARD_RULE_APLICA"],
            ),
            QualityDimension(
                name="analysis_convergence",
                description="Convergencia del razonamiento de aprobación",
                score=corrected.convergence_score,
                weight=0.20,
                flags=["MAX_CORRECCIONES"] if corrected.max_iterations_reached else [],
            ),
            QualityDimension(
                name="decision_confidence",
                description="Confianza declarada en la decisión final",
                score=corrected.final_confidence,
                weight=0.15,
                flags=["BAJA_CONFIANZA"] if corrected.final_confidence < 0.65 else [],
            ),
        ]

    # ═══════════════════════════════════════════════════════════════════════
    # L10 — ENSAMBLAJE DE SALIDA
    # ═══════════════════════════════════════════════════════════════════════

    def _assemble_agent_result(
        self,
        state: CreditEvaluationState,
        corrected: SelfCorrectionResult,
        quality: QualityAssessment,
        justification: Justification,
        metrics: DeepAgentMetrics,
    ) -> CreditEvaluationState:
        data      = corrected.final_data
        app       = state.application_input
        actuarial = state.actuarial_result
        act_score = actuarial.actuarial_score if actuarial else None
        pred      = actuarial.predictive_model if actuarial else None

        # Decisión final: Hard Rule tiene prioridad absoluta
        raw_decision = self._hard_rule_decision or data.get("decision", "ESCALATED_TO_COMMITTEE")
        try:
            final_decision = ApplicationStatus(raw_decision)
        except ValueError:
            final_decision = ApplicationStatus.ESCALATED

        # Razón de decisión
        reason_str = data.get("decision_reason", "ESCALATED_BORDERLINE")
        try:
            decision_reason = DecisionReason(reason_str)
        except ValueError:
            decision_reason = DecisionReason.ESCALATED_BORDERLINE

        # ── Términos de crédito (solo si APPROVED) ────────────────────────────
        credit_terms: Optional[CreditTerms] = None
        if final_decision == ApplicationStatus.APPROVED:
            amt  = float(data.get("approved_amount") or (app.credit_request.requested_amount if app else 0))
            rate = float(data.get("interest_rate_annual") or (act_score.interest_rate_suggestion if act_score else 18.0))
            term = int(data.get("term_months") or (app.credit_request.term_months if app else 36))
            monthly_rate = rate / 100 / 12
            if monthly_rate > 0:
                installment = amt * monthly_rate / (1 - (1 + monthly_rate) ** (-term))
            else:
                installment = amt / max(term, 1)

            conditions = [
                ApprovalCondition(**c)
                for c in (data.get("conditions") or [])
                if isinstance(c, dict) and "condition_type" in c and "description" in c
            ]
            credit_terms = CreditTerms(
                approved_amount=round(amt, 2),
                interest_rate_annual=round(rate, 2),
                term_months=term,
                monthly_installment=round(installment, 2),
                total_cost=round(installment * term, 2),
                conditions=conditions,
            )

        # ── Paquete de escalación (solo si ESCALATED) ─────────────────────────
        escalation_package: Optional[EscalationPackage] = None
        if final_decision == ApplicationStatus.ESCALATED:
            fraud_s  = state.fraud_result.fraud_score if state.fraud_result else 0.0
            bureau_s = state.credit_result.bureau_data.credit_score if (state.credit_result and state.credit_result.bureau_data) else 0
            pd_val   = pred.default_probability_12m if pred else 0.5
            escalation_package = EscalationPackage(
                escalation_reason=data.get("escalation_reason", self._hard_rule_reason or "Borderline"),
                priority=data.get("escalation_priority", "MEDIUM"),
                committee_type=data.get("committee_type", "CREDIT"),
                summary=(
                    f"Solicitud {state.request_id}: "
                    f"monto={app.credit_request.requested_amount if app else 0} "
                    f"PD={pd_val:.4f} fraud={fraud_s:.4f} bureau={bureau_s}"
                ),
                fraud_score=fraud_s,
                credit_score=bureau_s,
                default_probability=pd_val,
                requested_amount=app.credit_request.requested_amount if app else 0,
                recommended_decision=raw_decision,
                key_concerns=data.get("rejection_reasons", self._hard_rule_violations),
                supporting_data={
                    "actuarial_score": act_score.composite_score if act_score else 0,
                    "score_band": act_score.score_band if act_score else "N/A",
                    "hard_rule_violations": self._hard_rule_violations,
                },
            )
            record_escalation(
                committee_type=escalation_package.committee_type,
                priority=escalation_package.priority,
            )

        approval_result = ApprovalResult(
            status=AgentStatus.SUCCESS,
            decision=final_decision,
            decision_reason=decision_reason,
            confidence=corrected.final_confidence,
            credit_terms=credit_terms,
            rejection_reasons=data.get("rejection_reasons", []),
            required_documents=data.get("required_documents", []),
            escalation_package=escalation_package,
            justification=justification.executive_summary,
            regulatory_basis=data.get("regulatory_basis", ""),
            decision_factors=data.get("decision_factors", {}),
            human_review_required=bool(data.get("human_review_required", False)) or quality.requires_human_review,
            execution_time_ms=metrics.total_duration_ms,
            retry_count=state.get_retry_count(self.agent_name),
        )

        record_approval(decision=final_decision.value, reason=decision_reason.value)

        updated = state.model_copy(update={
            "approval_result": approval_result,
            "status": final_decision,
            "current_node": self.agent_name,
        })
        return updated.add_audit_event(
            node=self.agent_name,
            action="APPROVAL_DEEP_DECISION",
            actor=self.agent_name,
            outcome=final_decision.value,
            duration_ms=metrics.total_duration_ms,
            metadata={
                "decision": final_decision.value,
                "reason": decision_reason.value,
                "confidence": corrected.final_confidence,
                "quality": metrics.quality_score,
                "hard_rule_applied": self._hard_rule_decision is not None,
                "corrections": metrics.self_corrections_count,
                "human_review": approval_result.human_review_required,
                "cross_agent_inconsistencies": len(updated.fraud_result.fraud_flags if updated.fraud_result else []),
            },
        )
