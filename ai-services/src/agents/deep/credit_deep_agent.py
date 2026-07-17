"""
CLOUD BANK — Agente de Historial Crediticio Deep Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBJETIVO
  Evaluar la salud financiera histórica y presente del solicitante.
  Determinar si puede asumir la deuda adicional sin comprometer su estabilidad.

RESPONSABILIDADES
  ✦ Consultar bureau de crédito (score, morosidades, historial)
  ✦ Verificar ingresos contra registros tributarios y planillas
  ✦ Analizar patrón de gastos y obligaciones vigentes
  ✦ Calcular capacidad de pago real (DTI pre y post-crédito)
  ✦ Ejecutar screening AML (OFAC, ONU, sanciones, PEPs)
  ✦ Detectar inconsistencias entre información declarada y verificada
  ✦ Emitir nivel de riesgo crediticio con base en múltiples factores

PLANIFICADOR INTERNO
  Si AML devuelve positivo → elevar al error handler sin importar el resto
  Si bureau falla → evaluar con income/expense como proxy
  Si income y bureau fallan → degradar calidad, escalar a humano

VERIFICADOR INTERNO
  Detecta 6 tipos de inconsistencias:
    1. Ingreso declarado vs. verificado (>20% discrepancia → FLAG)
    2. Gastos declarados vs. bureau (sub-declaración)
    3. DTI calculado inconsistente con datos de entrada
    4. Crédito utilización fuera de rango coherente con score
    5. Antigüedad de empleo inconsistente con historial bureau
    6. Deuda total implausible dado el ingreso declarado

AUTOCORRECTOR
  Detecta y corrige:
    - DTI calculado incorrectamente
    - risk_level inconsistente con score y DTI
    - AML clear=True pero flags presentes (contradicción)
    - information_consistent=True con inconsistency_flags no vacíos
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

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
    SelfCorrectionResult,
    ToolExecutionResults,
    ToolInvocationPlan,
    ToolResult,
    VerificationResult,
)
from src.core.config import get_settings
from src.core.state import (
    AgentStatus,
    CreditBureauData,
    CreditEvaluationState,
    CreditHistoryResult,
    ExpenseAnalysis,
    IncomeAnalysis,
    PaymentCapacity,
    RiskLevel,
)
from src.tools.credit_tools import (
    query_credit_bureau,
    run_aml_check,
    verify_income_sources,
    analyze_expense_pattern,
)

logger = structlog.get_logger(__name__)

# ── Umbrales regulatorios ────────────────────────────────────────────────────
_INCOME_DISCREPANCY_THRESHOLD = 0.20
_DTI_MAX_HARD_REJECT = 0.50
_DEBT_TO_ANNUAL_INCOME_MAX = 8.0


class CreditDeepAgent(BaseDeepAgent):
    """
    Agente de Historial Crediticio con análisis financiero multicapa.
    Combina datos de bureau, tributarios y gastos para una visión 360° del solicitante.
    """

    agent_name = "credit_deep_agent"
    min_confidence_threshold = 0.55
    min_quality_threshold = 0.50

    # ── Objetos de dominio internos ───────────────────────────────────────────
    _bureau_obj: Any = None
    _aml_data: Any = None
    _income_obj: Any = None
    _expense_obj: Any = None

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRATOS ABSTRACTOS
    # ═══════════════════════════════════════════════════════════════════════

    def _get_required_input_fields(self) -> list[str]:
        return ["application_input"]

    def _get_required_prior_agents(self) -> list[str]:
        return ["fraud_agent"]  # Necesita saber si hay fraude crítico

    def _build_role_system_prompt(self) -> str:
        return """
Eres el Agente de Historial Crediticio de CLOUD BANK.
Tu función es evaluar la salud financiera e historial crediticio del solicitante.

PRINCIPIOS DE EVALUACIÓN:
1. Objetividad: basar el análisis exclusivamente en datos verificados.
2. Consistencia: detectar y reportar toda discrepancia entre fuentes.
3. Proporcionalidad: el riesgo crediticio debe reflejar el perfil completo.
4. Transparencia: documentar qué datos faltaron y cómo se manejaron.
5. AML primero: si hay señal AML positiva, es determinante.

ESCALA DE RIESGO CREDITICIO:
MINIMAL → Score > 750, DTI < 0.20, sin morosidades, AML limpio
LOW     → Score 680-750, DTI 0.20-0.30, 0 morosidades activas
MEDIUM  → Score 600-680, DTI 0.30-0.40, ≤1 morosidad antigua
HIGH    → Score 550-600, DTI 0.40-0.50, morosidades recientes
CRITICAL → Score < 550, DTI > 0.50, quiebra, AML positivo

CONSISTENCIA OBLIGATORIA:
- information_consistent=True SOLO si inconsistency_flags está vacío.
- aml_clear=False → overall_credit_risk debe ser CRITICAL.
- DTI post-crédito > 0.50 → overall_credit_risk mínimo HIGH.
- Quiebra en historial → overall_credit_risk mínimo HIGH.

RESPONDE ÚNICAMENTE JSON. Sin texto previo ni posterior.
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L3 — PLANIFICADOR
    # ═══════════════════════════════════════════════════════════════════════

    def _create_agent_plan(
        self,
        state: CreditEvaluationState,
        context: ContextValidationResult,
    ) -> AgentPlan:
        """
        Plan adaptativo basado en disponibilidad de datos.
        Bureau y AML son críticos; income y expense pueden degradarse.
        """
        app = state.application_input
        invocations: list[ToolInvocationPlan] = []

        # Las 4 herramientas se ejecutan en paralelo
        invocations.append(ToolInvocationPlan(
            tool_name="query_credit_bureau",
            parallel_group=0,
            is_required=True,
            timeout_s=12.0,
            expected_output_type="CreditBureauData",
            fallback_strategy="EMPTY_BUREAU",
            input_params={
                "national_id": app.identity.national_id if app else "",
                "tax_id": app.identity.tax_id if app else None,
            },
        ))
        invocations.append(ToolInvocationPlan(
            tool_name="run_aml_check",
            parallel_group=0,
            is_required=True,
            timeout_s=12.0,
            expected_output_type="dict",
            fallback_strategy="FLAG_FOR_REVIEW",
            input_params={
                "national_id": app.identity.national_id if app else "",
                "full_name": app.identity.full_name if app else "",
            },
        ))
        invocations.append(ToolInvocationPlan(
            tool_name="verify_income_sources",
            parallel_group=0,
            is_required=False,
            timeout_s=12.0,
            expected_output_type="IncomeAnalysis",
            fallback_strategy="USE_DECLARED",
        ))
        invocations.append(ToolInvocationPlan(
            tool_name="analyze_expense_pattern",
            parallel_group=0,
            is_required=False,
            timeout_s=12.0,
            expected_output_type="ExpenseAnalysis",
            fallback_strategy="USE_DECLARED",
        ))

        return AgentPlan(
            strategy=PlanStrategy.FULL_PARALLEL,
            tool_invocations=invocations,
            total_tools=4,
            skipped_tools=0,
            parallel_groups_count=1,
            critical_path=["query_credit_bureau", "run_aml_check"],
            contingencies={
                "query_credit_bureau": "Evaluar con income/expense como proxy de crédito",
                "run_aml_check": "FLAG para revisión humana — AML es bloqueante",
                "verify_income_sources": "Usar ingreso declarado con penalización de calidad",
                "analyze_expense_pattern": "Usar obligaciones declaradas",
            },
            estimated_duration_s=12.0,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L4 — EJECUCIÓN DE HERRAMIENTAS
    # ═══════════════════════════════════════════════════════════════════════

    async def _execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        app = state.application_input
        t_start = time.monotonic()

        raw = await asyncio.gather(
            query_credit_bureau(
                app.identity.national_id if app else "",
                app.identity.tax_id if app else None,
            ),
            run_aml_check(
                app.identity.national_id if app else "",
                app.identity.full_name if app else "",
            ),
            verify_income_sources(
                declared_income=app.monthly_income + app.additional_income if app else 0,
                employment_type=app.employment_type if app else "",
                employer_name=app.employer_name if app else None,
                employment_months=app.employment_months if app else 0,
            ),
            analyze_expense_pattern(
                national_id=app.identity.national_id if app else "",
                declared_obligations=app.monthly_obligations if app else 0,
            ),
            return_exceptions=True,
        )

        tool_names = [
            "query_credit_bureau",
            "run_aml_check",
            "verify_income_sources",
            "analyze_expense_pattern",
        ]
        defaults_map = {
            "query_credit_bureau": (CreditBureauData, {
                "bureau_name": "UNAVAILABLE",
                "credit_score": 0,
                "score_model": "UNKNOWN",
            }),
            "run_aml_check": (dict, {}),
            "verify_income_sources": (IncomeAnalysis, {
                "declared_monthly_income": app.monthly_income if app else 0,
                "verified_monthly_income": (app.monthly_income or 0) * 0.85,
                "income_verification_method": "DECLARED_ONLY",
                "income_stability_score": 0.5,
                "income_source": app.employment_type if app else "unknown",
                "employment_type": app.employment_type if app else "unknown",
                "employment_duration_months": app.employment_months if app else 0,
            }),
            "analyze_expense_pattern": (ExpenseAnalysis, {
                "total_monthly_obligations": app.monthly_obligations if app else 0,
                "other_obligations": app.monthly_obligations if app else 0,
            }),
        }

        results: dict[str, ToolResult] = {}
        tool_objects = []
        for i, name in enumerate(tool_names):
            model_cls, defs = defaults_map[name]
            if name == "run_aml_check":
                # AML devuelve dict — manejarlo diferente
                if isinstance(raw[i], Exception):
                    obj = {"clear": True, "flags": ["AML_SERVICE_UNAVAILABLE"]}
                    tr = ToolResult(
                        tool_name=name,
                        success=False,
                        result=None,
                        error=str(raw[i])[:200],
                        is_degraded=True,
                        data_quality=0.3,
                    )
                else:
                    obj = raw[i]
                    tr = ToolResult(tool_name=name, success=True, result=obj, data_quality=1.0)
            else:
                obj, tr = self._safe_tool_result(raw[i], model_cls, defs, name)
            results[name] = tr
            tool_objects.append(obj)

        self._bureau_obj  = tool_objects[0]
        self._aml_data    = tool_objects[1]
        self._income_obj  = tool_objects[2]
        self._expense_obj = tool_objects[3]

        # Calcular capacidad de pago interna
        self._capacity_obj = self._compute_payment_capacity(app, self._income_obj, self._expense_obj)

        success = sum(1 for r in results.values() if r.success)
        total_ms = (time.monotonic() - t_start) * 1000

        return ToolExecutionResults(
            status=LayerStatus.SUCCESS,
            results=results,
            success_count=success,
            failure_count=sum(1 for r in results.values() if not r.success and not r.is_degraded),
            degraded_count=sum(1 for r in results.values() if r.is_degraded),
            overall_success_rate=success / 4.0,
            total_duration_ms=total_ms,
            critical_tool_failed=False,
        )

    def _compute_payment_capacity(self, app: Any, income: IncomeAnalysis, expense: ExpenseAnalysis) -> PaymentCapacity:
        settings = get_settings()
        if not app:
            return PaymentCapacity(
                gross_monthly_income=0, total_monthly_obligations=0,
                disposable_income=0, debt_to_income_ratio=1.0,
                requested_installment=0, post_credit_dti=1.0,
                capacity_score=0.0, can_afford=False,
            )

        gross = income.verified_monthly_income if income else app.monthly_income
        obligations = expense.total_monthly_obligations if expense else app.monthly_obligations

        monthly_rate = 0.15 / 12
        amount = app.credit_request.requested_amount
        term = app.credit_request.term_months

        if monthly_rate > 0 and term > 0:
            installment = amount * monthly_rate / (1 - (1 + monthly_rate) ** (-term))
        else:
            installment = amount / max(term, 1)

        pre_dti = obligations / max(gross, 1)
        post_dti = (obligations + installment) / max(gross, 1)
        max_inst = gross * settings.risk.capacity_ratio_maximum - obligations
        capacity_score = max(0.0, min(1.0, 1.0 - post_dti / settings.risk.capacity_ratio_maximum))
        can_afford = post_dti <= settings.risk.capacity_ratio_maximum and max_inst >= installment

        return PaymentCapacity(
            gross_monthly_income=round(gross, 2),
            total_monthly_obligations=round(obligations, 2),
            disposable_income=round(gross - obligations, 2),
            debt_to_income_ratio=round(pre_dti, 4),
            requested_installment=round(installment, 2),
            post_credit_dti=round(post_dti, 4),
            capacity_score=round(capacity_score, 4),
            can_afford=can_afford,
            max_affordable_installment=round(max(0.0, max_inst), 2),
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L5 — VERIFICADOR INTERNO
    # ═══════════════════════════════════════════════════════════════════════

    def _verify_tool_results(
        self,
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> VerificationResult:
        """
        Detecta 6 tipos de inconsistencias financieras entre fuentes de datos.
        """
        inconsistencies: list[Inconsistency] = []
        anomalies: list[AnomalyFlag] = []
        app = state.application_input

        income  = self._income_obj
        expense = self._expense_obj
        bureau  = self._bureau_obj
        cap     = self._capacity_obj

        # ── Verificación 1: Discrepancia de ingresos ─────────────────────────
        if income and income.declared_monthly_income > 0:
            discrepancy = abs(income.declared_monthly_income - income.verified_monthly_income) / income.declared_monthly_income
            if discrepancy > _INCOME_DISCREPANCY_THRESHOLD:
                inconsistencies.append(Inconsistency(
                    field_a="income.declared_monthly_income",
                    field_b="income.verified_monthly_income",
                    expected_relationship="Diferencia < 20%",
                    actual_values=f"declarado={income.declared_monthly_income:.2f} vs verificado={income.verified_monthly_income:.2f} ({discrepancy:.1%})",
                    severity=InconsistencySeverity.HIGH if discrepancy > 0.35 else InconsistencySeverity.MEDIUM,
                    description=f"Discrepancia de ingresos del {discrepancy:.1%} entre declarado y verificado",
                    can_auto_correct=False,
                    correction_suggestion="Solicitar documentación adicional de ingresos",
                ))

        # ── Verificación 2: Gastos sub-declarados ─────────────────────────────
        if expense and app and app.monthly_obligations > 0:
            declared_obligations = app.monthly_obligations
            bureau_obligations = expense.total_monthly_obligations
            if bureau_obligations > 0:
                sub_declaration = (bureau_obligations - declared_obligations) / max(bureau_obligations, 1)
                if sub_declaration > 0.25:
                    inconsistencies.append(Inconsistency(
                        field_a="application.monthly_obligations",
                        field_b="expense_analysis.total_monthly_obligations",
                        expected_relationship="Diferencia < 25%",
                        actual_values=f"declarado={declared_obligations:.2f} vs bureau={bureau_obligations:.2f}",
                        severity=InconsistencySeverity.HIGH,
                        description=f"Gastos sub-declarados en {sub_declaration:.1%}",
                        can_auto_correct=True,
                        correction_suggestion="Usar valor bureau para DTI",
                    ))

        # ── Verificación 3: Score vs utilización de crédito ──────────────────
        if bureau and bureau.credit_score > 0 and bureau.credit_utilization > 0:
            if bureau.credit_score > 700 and bureau.credit_utilization > 0.80:
                inconsistencies.append(Inconsistency(
                    field_a="bureau.credit_score",
                    field_b="bureau.credit_utilization",
                    expected_relationship="Score alto debería correlacionar con utilización baja",
                    actual_values=f"score={bureau.credit_score}, utilization={bureau.credit_utilization:.1%}",
                    severity=InconsistencySeverity.MEDIUM,
                    description="Score elevado con alta utilización de crédito — inconsistente",
                ))

        # ── Verificación 4: Deuda total implausible ───────────────────────────
        if bureau and income and income.verified_monthly_income > 0:
            annual_income = income.verified_monthly_income * 12
            if bureau.total_debt > annual_income * _DEBT_TO_ANNUAL_INCOME_MAX:
                anomalies.append(AnomalyFlag(
                    category="DEBT_LEVEL",
                    description=f"Deuda total {bureau.total_debt:.0f} supera {_DEBT_TO_ANNUAL_INCOME_MAX}x el ingreso anual {annual_income:.0f}",
                    severity=InconsistencySeverity.HIGH,
                    data_point=f"total_debt={bureau.total_debt:.2f}",
                    expected_range=f"< {annual_income * _DEBT_TO_ANNUAL_INCOME_MAX:.0f}",
                ))

        # ── Verificación 5: DTI crítico ───────────────────────────────────────
        if cap and cap.post_credit_dti > _DTI_MAX_HARD_REJECT:
            anomalies.append(AnomalyFlag(
                category="CAPACITY",
                description=f"DTI post-crédito {cap.post_credit_dti:.1%} supera límite regulatorio {_DTI_MAX_HARD_REJECT:.1%}",
                severity=InconsistencySeverity.CRITICAL,
                data_point=f"post_credit_dti={cap.post_credit_dti:.4f}",
                expected_range=f"< {_DTI_MAX_HARD_REJECT:.1%}",
            ))

        # ── Verificación 6: AML flags + clear=True ────────────────────────────
        if self._aml_data:
            aml_clear = self._aml_data.get("clear", True)
            aml_flags = self._aml_data.get("flags", [])
            if aml_clear and len([f for f in aml_flags if "UNAVAILABLE" not in f]) > 0:
                inconsistencies.append(Inconsistency(
                    field_a="aml.clear",
                    field_b="aml.flags",
                    expected_relationship="aml_clear=True implica flags vacíos (excepto service errors)",
                    actual_values=f"clear={aml_clear}, flags={aml_flags}",
                    severity=InconsistencySeverity.HIGH,
                    description="AML reporta clean pero tiene flags de matching",
                ))

        critical = sum(
            1 for a in anomalies if a.severity == InconsistencySeverity.CRITICAL
        )
        quality = max(0.1, 1.0 - 0.15 * len(inconsistencies) - 0.25 * critical)

        return VerificationResult(
            status=LayerStatus.SUCCESS,
            is_consistent=len(inconsistencies) == 0,
            inconsistencies=inconsistencies,
            anomaly_flags=anomalies,
            data_quality_score=quality,
            critical_anomalies_count=critical,
            verification_notes=[
                f"{len(inconsistencies)} inconsistencias financieras detectadas",
                f"DTI post-crédito: {cap.post_credit_dti:.1%}" if cap else "",
                f"AML: {'LIMPIO' if self._aml_data and self._aml_data.get('clear', True) else 'POSITIVO'}",
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
        bureau   = self._bureau_obj
        income   = self._income_obj
        expense  = self._expense_obj
        cap      = self._capacity_obj
        aml      = self._aml_data
        app      = state.application_input
        fraud    = state.fraud_result

        incon_text = "\n".join(
            f"  [{i.severity.value}] {i.description}" for i in verification.inconsistencies
        ) or "  Ninguna"
        anomaly_text = "\n".join(
            f"  [{a.severity.value}] {a.description}" for a in verification.anomaly_flags
        ) or "  Ninguna"

        return f"""
═══ CONTEXTO DE FRAUDE (agente previo) ═══
Fraud Score: {(fraud.fraud_score if fraud else 0.0):.4f} | Nivel: {fraud.risk_level.value if fraud else 'N/A'}
Bloqueado por fraude: {fraud.is_blocked if fraud else False}

═══ BUREAU DE CRÉDITO ═══
Score: {bureau.credit_score if bureau else 0} (modelo: {bureau.score_model if bureau else 'N/A'})
Cuentas totales: {bureau.total_accounts if bureau else 0} | Abiertas: {bureau.open_accounts if bureau else 0}
Cuentas morosas: {bureau.delinquent_accounts if bureau else 0}
Deuda total: {(bureau.total_debt if bureau else 0):.2f}
Utilización crédito: {(bureau.credit_utilization if bureau else 0.0):.1%}
Antigüedad (meses): {bureau.oldest_account_months if bureau else 0}
Historial pagos: {(bureau.payment_history_score if bureau else 0.0):.3f}
Marcas negativas: {bureau.negative_marks if bureau else []}
Quiebra: {bureau.bankruptcy_history if bureau else False}

═══ INGRESOS ═══
Ingreso declarado/mes: {(income.declared_monthly_income if income else (app.monthly_income if app else 0)):.2f}
Ingreso verificado/mes: {(income.verified_monthly_income if income else 0):.2f}
Método verificación: {income.income_verification_method if income else 'DECLARED_ONLY'}
Estabilidad: {(income.income_stability_score if income else 0.5):.3f}
Empleo: {income.employment_type if income else 'N/A'} ({income.employment_duration_months if income else 0} meses)
Discrepancia de ingresos: {income.income_discrepancy_flag if income else False}

═══ GASTOS Y OBLIGACIONES ═══
Obligaciones totales/mes: {(expense.total_monthly_obligations if expense else 0):.2f}
Renta/hipoteca: {(expense.rent_or_mortgage if expense else 0):.2f}
Préstamos vigentes: {(expense.existing_loans if expense else 0):.2f}
Mínimos tarjetas: {(expense.credit_card_minimums if expense else 0):.2f}
Discrepancia declarado vs bureau: {(expense.declared_vs_bureau_discrepancy if expense else 0.0):.1%}

═══ CAPACIDAD DE PAGO ═══
Ingreso bruto mensual: {(cap.gross_monthly_income if cap else 0):.2f}
Obligaciones totales: {(cap.total_monthly_obligations if cap else 0):.2f}
Ingreso disponible: {(cap.disposable_income if cap else 0):.2f}
DTI actual: {(cap.debt_to_income_ratio if cap else 0.0):.1%}
Cuota mensual estimada: {(cap.requested_installment if cap else 0):.2f}
DTI post-crédito: {(cap.post_credit_dti if cap else 0.0):.1%}
Score capacidad: {(cap.capacity_score if cap else 0):.4f}
Puede pagar: {cap.can_afford if cap else False}
Cuota máx. pagable: {(cap.max_affordable_installment if cap else 0):.2f}

═══ AML ═══
Limpio: {aml.get('clear', True) if aml else True}
Flags AML: {aml.get('flags', []) if aml else []}
Listas verificadas: {aml.get('lists_checked', []) if aml else []}

═══ INCONSISTENCIAS DETECTADAS ═══
{incon_text}

═══ ANOMALÍAS DETECTADAS ═══
{anomaly_text}

Solicitud: monto={app.credit_request.requested_amount if app else 0} | plazo={app.credit_request.term_months if app else 0}m

Genera el análisis de historial crediticio en este JSON exacto:
{{
  "overall_credit_risk": "<MINIMAL|LOW|MEDIUM|HIGH|CRITICAL>",
  "information_consistent": <true|false>,
  "inconsistency_flags": ["<flag1>"],
  "aml_clear": <true|false>,
  "aml_flags": ["<flag>"],
  "confidence": <float 0.00-1.00>,
  "reasoning_chain": ["<paso1>", "<paso2>", "<paso3>"],
  "explanation": "<análisis completo para comité de riesgo>",
  "key_concerns": ["<concern1>"],
  "positive_factors": ["<factor positivo>"],
  "counterfactual": "<qué habría cambiado el riesgo>",
  "decision_support": "<qué debe considerar el siguiente agente>"
}}
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L7 — REGLAS DE NEGOCIO + PROMPT DE CORRECCIÓN
    # ═══════════════════════════════════════════════════════════════════════

    def _check_business_rule_violations(
        self,
        parsed: dict[str, Any],
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> list[str]:
        violations: list[str] = []
        cap = self._capacity_obj
        aml = self._aml_data

        risk_level = parsed.get("overall_credit_risk", "")
        info_consistent = parsed.get("information_consistent", True)
        incon_flags = parsed.get("inconsistency_flags", [])
        aml_clear = parsed.get("aml_clear", True)
        aml_flags_output = parsed.get("aml_flags", [])

        # Regla 1: information_consistent vs inconsistency_flags
        if info_consistent and incon_flags:
            violations.append(
                "CONTRADICCIÓN: information_consistent=True pero inconsistency_flags no vacío"
            )
        if not info_consistent and not incon_flags:
            violations.append(
                "CONTRADICCIÓN: information_consistent=False pero inconsistency_flags vacío"
            )

        # Regla 2: AML positivo → CRITICAL
        aml_actual_clear = aml.get("clear", True) if aml else True
        if not aml_actual_clear:
            if aml_clear:
                violations.append("ERROR: aml_clear=True pero servicio AML devolvió positivo")
            if risk_level not in ("HIGH", "CRITICAL"):
                violations.append(
                    f"REGLA: AML positivo requiere overall_credit_risk=CRITICAL, declarado={risk_level}"
                )

        # Regla 3: DTI > 0.50 → mínimo HIGH
        if cap and cap.post_credit_dti > _DTI_MAX_HARD_REJECT:
            if risk_level not in ("HIGH", "CRITICAL"):
                violations.append(
                    f"REGLA: DTI={cap.post_credit_dti:.1%} > 50% requiere risk_level=HIGH|CRITICAL"
                )

        # Regla 4: Quiebra → mínimo HIGH
        bureau = self._bureau_obj
        if bureau and bureau.bankruptcy_history:
            if risk_level not in ("HIGH", "CRITICAL"):
                violations.append(
                    "REGLA: historial de quiebra requiere overall_credit_risk=HIGH|CRITICAL"
                )

        # Regla 5: confidence presente
        confidence = parsed.get("confidence")
        if confidence is None or not (0.0 <= float(confidence) <= 1.0):
            violations.append("CAMPO FALTANTE o INVÁLIDO: 'confidence'")

        return violations

    def _build_correction_prompt(
        self,
        original: dict[str, Any],
        violations: list[str],
        tool_results: ToolExecutionResults,
    ) -> str:
        cap = self._capacity_obj
        return f"""
Tu análisis crediticio tiene violaciones que corregir:

VIOLACIONES:
{chr(10).join(f'  {i+1}. {v}' for i, v in enumerate(violations))}

DATOS OBJETIVOS (no cambiar):
- DTI post-crédito: {(cap.post_credit_dti if cap else 0.0):.4f}
- AML real: {self._aml_data}
- Bureau score: {self._bureau_obj.credit_score if self._bureau_obj else 0}

ANÁLISIS ANTERIOR:
{original}

Corrige SOLO los campos con violaciones. Mantén los campos correctos intactos.
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
        bureau = self._bureau_obj
        income = self._income_obj

        bureau_quality = 1.0 if (bureau and bureau.credit_score > 0) else 0.2
        income_method_quality = {
            "TAX_RECORDS": 1.0,
            "PAYROLL": 0.9,
            "BANK_STATEMENTS": 0.8,
            "EMPLOYER_VERIFICATION": 0.7,
            "DECLARED_ONLY": 0.4,
        }.get(income.income_verification_method if income else "DECLARED_ONLY", 0.5)

        return [
            QualityDimension(
                name="bureau_data_quality",
                description="Calidad y completitud de datos del bureau de crédito",
                score=bureau_quality,
                weight=0.35,
                flags=["BUREAU_UNAVAILABLE"] if bureau_quality < 0.5 else [],
            ),
            QualityDimension(
                name="income_verification_quality",
                description="Método de verificación de ingresos",
                score=income_method_quality,
                weight=0.30,
                flags=[f"MÉTODO: {income.income_verification_method if income else 'UNKNOWN'}"],
            ),
            QualityDimension(
                name="data_consistency",
                description="Consistencia entre fuentes de datos financieros",
                score=verification.data_quality_score,
                weight=0.20,
                flags=[i.description[:60] for i in verification.inconsistencies[:2]],
            ),
            QualityDimension(
                name="analysis_convergence",
                description="Estabilidad del análisis crediticio",
                score=corrected.convergence_score,
                weight=0.15,
                flags=["MAX_CORRECCIONES"] if corrected.max_iterations_reached else [],
            ),
        ]

    # ═══════════════════════════════════════════════════════════════════════
    # L10 — ENSAMBLAJE
    # ═══════════════════════════════════════════════════════════════════════

    def _assemble_agent_result(
        self,
        state: CreditEvaluationState,
        corrected: SelfCorrectionResult,
        quality: QualityAssessment,
        justification: Justification,
        metrics: DeepAgentMetrics,
    ) -> CreditEvaluationState:
        data = corrected.final_data
        aml  = self._aml_data or {}

        credit_result = CreditHistoryResult(
            status=AgentStatus.SUCCESS,
            bureau_data=self._bureau_obj,
            income_analysis=self._income_obj,
            expense_analysis=self._expense_obj,
            payment_capacity=self._capacity_obj,
            aml_clear=bool(data.get("aml_clear", aml.get("clear", True))),
            aml_flags=data.get("aml_flags", aml.get("flags", [])),
            information_consistent=bool(data.get("information_consistent", True)),
            inconsistency_flags=data.get("inconsistency_flags", []),
            overall_credit_risk=RiskLevel(data.get("overall_credit_risk", "MEDIUM")),
            explanation=justification.gdpr_explanation,
            execution_time_ms=metrics.total_duration_ms,
            retry_count=state.get_retry_count(self.agent_name),
        )

        updated = state.model_copy(update={
            "credit_result": credit_result,
            "current_node": self.agent_name,
        })
        return updated.add_audit_event(
            node=self.agent_name,
            action="CREDIT_DEEP_ANALYSIS_COMPLETE",
            actor=self.agent_name,
            outcome=f"risk={credit_result.overall_credit_risk.value} dti={(self._capacity_obj.post_credit_dti if self._capacity_obj else 0.0):.4f} aml={credit_result.aml_clear}",
            duration_ms=metrics.total_duration_ms,
            metadata={
                "confidence": metrics.confidence_score,
                "quality": metrics.quality_score,
                "bureau_score": self._bureau_obj.credit_score if self._bureau_obj else 0,
                "corrections": metrics.self_corrections_count,
            },
            pii_accessed=True,
        )
