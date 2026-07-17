"""
CLOUD BANK — Agente Actuario Deep Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBJETIVO
  Calcular con precisión la probabilidad de impago (PD) y el riesgo actuarial
  del solicitante usando un ensemble de modelos estadísticos + LLM como sintetizador.
  Garantizar que cada variable usada, cada decisión y cada cálculo sea auditable
  bajo Basel III / SR 11-7.

RESPONSABILIDADES
  ✦ Construir el feature vector desde los resultados de agentes previos
  ✦ Ejecutar 3 modelos predictivos en paralelo (LR, GBM, NN)
  ✦ Calcular ensemble ponderado y detectar divergencia entre modelos
  ✦ Computar SHAP values para explicabilidad regulatoria
  ✦ Estimar Expected Loss, Unexpected Loss y Risk-Weighted Assets (Basel III)
  ✦ Generar score actuarial compuesto (0-1000)
  ✦ Documentar variables, metodología y limitaciones del modelo

VERIFICADOR INTERNO — detecta:
  1. Divergencia extrema entre modelos (> 0.25) → FLAG
  2. PD ensemble fuera del rango coherente dado el score del bureau
  3. SHAP values con suma incoherente
  4. Tasa de interés sugerida por debajo del costo de fondos (no viable)
  5. Monto máximo recomendado > lo que el DTI permite
  6. score_band incoherente con composite_score

AUTOCORRECTOR — detecta y corrige:
  1. composite_score fuera de [0, 1000]
  2. score_band no alineado con composite_score
  3. interest_rate_suggestion < 5% (inviable) o > 60% (ilegal)
  4. max_recommended_amount > lo que permite el DTI
  5. PD > 0.70 con risk_category < HIGH (contradicción)
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
    ActuarialResult,
    ActuarialScore,
    CreditEvaluationState,
    PredictiveModelOutput,
    RiskLevel,
)
from src.observability.metrics import record_default_probability
from src.tools.actuarial_tools import (
    run_logistic_regression_model,
    run_gradient_boosting_model,
    run_neural_network_model,
    compute_ensemble_score,
    compute_shap_values,
    estimate_loss_metrics,
)

logger = structlog.get_logger(__name__)

# ── Bandas de score actuarial (similar a S&P/Moody's para crédito retail) ───
_SCORE_BANDS = [
    (850, 1000, "AA",  RiskLevel.MINIMAL, (5.0,  10.0)),
    (700, 849,  "A",   RiskLevel.LOW,     (10.0, 15.0)),
    (600, 699,  "BBB", RiskLevel.MEDIUM,  (15.0, 22.0)),
    (500, 599,  "BB",  RiskLevel.MEDIUM,  (22.0, 30.0)),
    (400, 499,  "B",   RiskLevel.HIGH,    (30.0, 40.0)),
    (300, 399,  "CCC", RiskLevel.HIGH,    (40.0, 55.0)),
    (0,   299,  "D",   RiskLevel.CRITICAL,(55.0, 99.0)),
]

_ENSEMBLE_WEIGHTS = {"lr": 0.30, "gbm": 0.45, "nn": 0.25}
_MAX_MODEL_DIVERGENCE = 0.25


def _pd_to_composite_score(pd: float) -> float:
    """Convierte PD (0-1) a score actuarial (0-1000). Inversa: mayor PD = menor score."""
    return max(0.0, min(1000.0, (1.0 - pd) * 1000.0))


def _composite_to_band(score: float):
    for lo, hi, band, risk, rate_range in _SCORE_BANDS:
        if lo <= score <= hi:
            return band, risk, rate_range
    return "D", RiskLevel.CRITICAL, (55.0, 99.0)


class ActuarialDeepAgent(BaseDeepAgent):
    """
    Agente Actuario Deep Agent.
    Combina 3 modelos ML con ensemble + LLM para evaluación de riesgo de crédito
    regulatoriamente documentada.
    """

    agent_name = "actuarial_deep_agent"
    min_confidence_threshold = 0.55
    min_quality_threshold = 0.50

    # Resultados intermedios de modelos
    _lr_pd: float = 0.5
    _gbm_pd: float = 0.5
    _nn_pd: float = 0.5
    _ensemble_pd: float = 0.5
    _shap_values: dict = {}
    _loss_metrics: dict = {}
    _features: dict = {}
    _model_divergence: float = 0.0

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRATOS ABSTRACTOS
    # ═══════════════════════════════════════════════════════════════════════

    def _get_required_input_fields(self) -> list[str]:
        return ["application_input"]

    def _get_required_prior_agents(self) -> list[str]:
        return ["fraud_agent", "credit_agent"]

    def _build_role_system_prompt(self) -> str:
        return """
Eres el Agente Actuario de CLOUD BANK. Tu responsabilidad es evaluar el riesgo cuantitativo de crédito.

PRINCIPIOS:
1. Precisión: la probabilidad de impago debe reflejar el riesgo real, no ser ni optimista ni pesimista.
2. Documentación: cada variable usada debe estar justificada (Basel III / SR 11-7).
3. Explicabilidad: los SHAP values deben explicar la contribución de cada variable.
4. Proporcionalidad: la tasa y el monto sugeridos deben ser económicamente viables.
5. Divergencia: si los modelos divergen significativamente, declararlo explícitamente.

BANDAS DE SCORE ACTUARIAL (0-1000):
850-1000 → AA (MINIMAL) | Tasa: 5-10%
700-849  → A  (LOW)     | Tasa: 10-15%
600-699  → BBB (MEDIUM) | Tasa: 15-22%
500-599  → BB  (MEDIUM) | Tasa: 22-30%
400-499  → B   (HIGH)   | Tasa: 30-40%
300-399  → CCC (HIGH)   | Tasa: 40-55%
0-299    → D   (CRITICAL)| Tasa: no viable

CONSISTENCIA OBLIGATORIA:
- composite_score ∈ [0, 1000]
- score_band DEBE corresponder al rango del composite_score
- risk_category DEBE corresponder al score_band
- interest_rate_suggestion ∈ [5.0, 60.0] (porcentaje anual)
- max_recommended_amount ≤ lo que permite el DTI del solicitante
- Si PD > 0.70 → risk_category = CRITICAL, score < 300
- confidence ∈ [0.0, 1.0]

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
        Todos los modelos corren en paralelo en el grupo 0.
        SHAP y métricas de pérdida necesitan el ensemble → grupo 1.
        """
        return AgentPlan(
            strategy=PlanStrategy.GROUPED_PARALLEL,
            tool_invocations=[
                ToolInvocationPlan(
                    tool_name="logistic_regression",
                    parallel_group=0,
                    is_required=True,
                    timeout_s=5.0,
                    expected_output_type="float",
                    fallback_strategy="USE_DEFAULT_PD_0.5",
                ),
                ToolInvocationPlan(
                    tool_name="gradient_boosting",
                    parallel_group=0,
                    is_required=True,
                    timeout_s=5.0,
                    expected_output_type="float",
                    fallback_strategy="USE_DEFAULT_PD_0.5",
                ),
                ToolInvocationPlan(
                    tool_name="neural_network",
                    parallel_group=0,
                    is_required=False,
                    timeout_s=8.0,
                    expected_output_type="float",
                    fallback_strategy="USE_LR_GBM_AVERAGE",
                ),
                ToolInvocationPlan(
                    tool_name="ensemble_score",
                    parallel_group=1,
                    is_required=True,
                    timeout_s=2.0,
                    expected_output_type="float",
                ),
                ToolInvocationPlan(
                    tool_name="shap_values",
                    parallel_group=1,
                    is_required=False,
                    timeout_s=5.0,
                    expected_output_type="dict",
                ),
                ToolInvocationPlan(
                    tool_name="loss_metrics",
                    parallel_group=1,
                    is_required=True,
                    timeout_s=2.0,
                    expected_output_type="dict",
                ),
            ],
            total_tools=6,
            skipped_tools=0,
            parallel_groups_count=2,
            critical_path=["logistic_regression", "gradient_boosting", "ensemble_score", "loss_metrics"],
            contingencies={
                "neural_network": "Reemplazar por promedio de LR y GBM",
                "shap_values": "Usar coeficientes LR como proxy de importancia",
            },
            estimated_duration_s=10.0,
            execution_notes=["Grupo 0: 3 modelos paralelos", "Grupo 1: ensemble + SHAP + loss"],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L4 — EJECUCIÓN DE HERRAMIENTAS
    # ═══════════════════════════════════════════════════════════════════════

    async def _execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        t_start = time.monotonic()

        # Construir feature vector
        self._features = self._build_feature_vector(state)

        # ── Grupo 0: 3 modelos en paralelo ────────────────────────────────────
        lr_raw, gbm_raw, nn_raw = await asyncio.gather(
            run_logistic_regression_model(self._features),
            run_gradient_boosting_model(self._features),
            run_neural_network_model(self._features),
            return_exceptions=True,
        )

        self._lr_pd  = lr_raw  if not isinstance(lr_raw,  Exception) else 0.5
        self._gbm_pd = gbm_raw if not isinstance(gbm_raw, Exception) else 0.5
        self._nn_pd  = nn_raw  if not isinstance(nn_raw,  Exception) else (self._lr_pd + self._gbm_pd) / 2

        # ── Grupo 1: Ensemble + SHAP + Loss (paralelo) ────────────────────────
        ensemble_raw, shap_raw, loss_raw = await asyncio.gather(
            compute_ensemble_score(self._lr_pd, self._gbm_pd, self._nn_pd),
            compute_shap_values(self._features),
            estimate_loss_metrics(
                pd=(self._lr_pd * 0.30 + self._gbm_pd * 0.45 + self._nn_pd * 0.25),
                ead=state.application_input.credit_request.requested_amount if state.application_input else 0,
                lgd=0.45,
            ),
            return_exceptions=True,
        )

        self._ensemble_pd  = ensemble_raw if not isinstance(ensemble_raw, Exception) else (self._lr_pd + self._gbm_pd + self._nn_pd) / 3
        self._shap_values  = shap_raw     if not isinstance(shap_raw,     Exception) else {}
        self._loss_metrics = loss_raw     if not isinstance(loss_raw,     Exception) else {}

        # Divergencia entre modelos
        self._model_divergence = max(
            abs(self._lr_pd  - self._gbm_pd),
            abs(self._gbm_pd - self._nn_pd),
            abs(self._lr_pd  - self._nn_pd),
        )

        record_default_probability(self._ensemble_pd)

        # Construir ToolResults
        results: dict[str, ToolResult] = {
            "logistic_regression": ToolResult(
                tool_name="logistic_regression",
                success=not isinstance(lr_raw, Exception),
                result=self._lr_pd,
                error=str(lr_raw) if isinstance(lr_raw, Exception) else None,
                data_quality=1.0 if not isinstance(lr_raw, Exception) else 0.4,
            ),
            "gradient_boosting": ToolResult(
                tool_name="gradient_boosting",
                success=not isinstance(gbm_raw, Exception),
                result=self._gbm_pd,
                error=str(gbm_raw) if isinstance(gbm_raw, Exception) else None,
                data_quality=1.0 if not isinstance(gbm_raw, Exception) else 0.4,
            ),
            "neural_network": ToolResult(
                tool_name="neural_network",
                success=not isinstance(nn_raw, Exception),
                result=self._nn_pd,
                data_quality=1.0 if not isinstance(nn_raw, Exception) else 0.5,
                is_degraded=isinstance(nn_raw, Exception),
            ),
            "ensemble_score": ToolResult(
                tool_name="ensemble_score",
                success=not isinstance(ensemble_raw, Exception),
                result=self._ensemble_pd,
                data_quality=1.0 if not isinstance(ensemble_raw, Exception) else 0.5,
            ),
            "shap_values": ToolResult(
                tool_name="shap_values",
                success=not isinstance(shap_raw, Exception),
                result=self._shap_values,
                is_degraded=not bool(self._shap_values),
                data_quality=0.8 if self._shap_values else 0.3,
            ),
            "loss_metrics": ToolResult(
                tool_name="loss_metrics",
                success=not isinstance(loss_raw, Exception),
                result=self._loss_metrics,
                data_quality=1.0 if not isinstance(loss_raw, Exception) else 0.4,
            ),
        }

        success = sum(1 for r in results.values() if r.success)
        total_ms = (time.monotonic() - t_start) * 1000

        return ToolExecutionResults(
            status=LayerStatus.SUCCESS,
            results=results,
            success_count=success,
            failure_count=sum(1 for r in results.values() if not r.success and not r.is_degraded),
            degraded_count=sum(1 for r in results.values() if r.is_degraded),
            overall_success_rate=success / 6.0,
            total_duration_ms=total_ms,
            critical_tool_failed=False,
        )

    def _build_feature_vector(self, state: CreditEvaluationState) -> dict[str, float]:
        app     = state.application_input
        fraud   = state.fraud_result
        credit  = state.credit_result
        bureau  = credit.bureau_data   if credit else None
        cap     = credit.payment_capacity if credit else None
        income  = credit.income_analysis  if credit else None

        return {
            "credit_score":             float(bureau.credit_score if bureau and bureau.credit_score else 0) / 1000.0,
            "credit_utilization":       float(bureau.credit_utilization if bureau else 0.5),
            "delinquent_accounts":      float(bureau.delinquent_accounts if bureau else 0),
            "oldest_account_months":    float(bureau.oldest_account_months if bureau else 0) / 360.0,
            "payment_history_score":    float(bureau.payment_history_score if bureau else 0.5),
            "bankruptcy_history":       float(1 if bureau and bureau.bankruptcy_history else 0),
            "post_credit_dti":          float(cap.post_credit_dti if cap else 0.5),
            "capacity_score":           float(cap.capacity_score if cap else 0.5),
            "income_stability":         float(income.income_stability_score if income else 0.5),
            "employment_months":        float(app.employment_months if app else 0) / 120.0,
            "fraud_score":              float(fraud.fraud_score if fraud else 0.5),
            "requested_amount":         float(app.credit_request.requested_amount if app else 0) / 500_000.0,
            "term_months":              float(app.credit_request.term_months if app else 36) / 84.0,
            "monthly_income_norm":      float(app.monthly_income if app else 1) / 10_000.0,
            "amount_to_income_ratio":   float(
                app.credit_request.requested_amount / max(app.monthly_income * 12, 1) if app else 0.5
            ),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # L5 — VERIFICADOR INTERNO
    # ═══════════════════════════════════════════════════════════════════════

    def _verify_tool_results(
        self,
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> VerificationResult:
        inconsistencies: list[Inconsistency] = []
        anomalies: list[AnomalyFlag] = []
        credit = state.credit_result
        bureau = credit.bureau_data if credit else None
        cap    = credit.payment_capacity if credit else None

        # ── V1: Divergencia extrema entre modelos ─────────────────────────────
        if self._model_divergence > _MAX_MODEL_DIVERGENCE:
            anomalies.append(AnomalyFlag(
                category="MODEL_DIVERGENCE",
                description=f"Divergencia máxima {self._model_divergence:.4f} supera umbral {_MAX_MODEL_DIVERGENCE}",
                severity=InconsistencySeverity.HIGH,
                data_point=f"LR={self._lr_pd:.4f} GBM={self._gbm_pd:.4f} NN={self._nn_pd:.4f}",
                expected_range=f"divergencia < {_MAX_MODEL_DIVERGENCE}",
                actual_value=f"{self._model_divergence:.4f}",
            ))

        # ── V2: PD vs score bureau ─────────────────────────────────────────────
        if bureau and bureau.credit_score > 0:
            bureau_implies_good = bureau.credit_score > 700
            model_implies_bad   = self._ensemble_pd > 0.50
            if bureau_implies_good and model_implies_bad:
                inconsistencies.append(Inconsistency(
                    field_a="bureau.credit_score",
                    field_b="ensemble_pd",
                    expected_relationship="Score > 700 debería correlacionar con PD < 0.30",
                    actual_values=f"score={bureau.credit_score}, pd={self._ensemble_pd:.4f}",
                    severity=InconsistencySeverity.MEDIUM,
                    description="Bureau score alto pero PD model alta — verificar features",
                    can_auto_correct=False,
                ))

        # ── V3: SHAP values coherentes ─────────────────────────────────────────
        if self._shap_values:
            shap_sum = sum(abs(v) for v in self._shap_values.values())
            if shap_sum > 5.0:
                anomalies.append(AnomalyFlag(
                    category="SHAP_COHERENCE",
                    description=f"SHAP values suman {shap_sum:.2f} — posible error de escala",
                    severity=InconsistencySeverity.LOW,
                    data_point=f"sum_abs_shap={shap_sum:.4f}",
                ))

        # ── V4: PD extrema coherente con features ─────────────────────────────
        if self._ensemble_pd > 0.90:
            anomalies.append(AnomalyFlag(
                category="EXTREME_PD",
                description=f"PD ensemble {self._ensemble_pd:.4f} es extremadamente alta — verificar features",
                severity=InconsistencySeverity.HIGH,
                data_point=f"ensemble_pd={self._ensemble_pd:.4f}",
            ))
        elif self._ensemble_pd < 0.01:
            anomalies.append(AnomalyFlag(
                category="EXTREME_PD",
                description=f"PD ensemble {self._ensemble_pd:.4f} es extremadamente baja — posible error",
                severity=InconsistencySeverity.MEDIUM,
                data_point=f"ensemble_pd={self._ensemble_pd:.4f}",
            ))

        # ── V5: max_recommended vs DTI ────────────────────────────────────────
        # (Se verifica en L7 contra el output del LLM)

        critical = sum(1 for a in anomalies if a.severity == InconsistencySeverity.CRITICAL)
        quality = max(0.1, 1.0 - 0.10 * len(inconsistencies) - 0.20 * critical)
        if self._model_divergence > _MAX_MODEL_DIVERGENCE:
            quality -= 0.15
        quality = max(0.1, min(1.0, quality))

        return VerificationResult(
            status=LayerStatus.SUCCESS,
            is_consistent=len(inconsistencies) == 0,
            inconsistencies=inconsistencies,
            anomaly_flags=anomalies,
            data_quality_score=quality,
            critical_anomalies_count=critical,
            verification_notes=[
                f"Divergencia entre modelos: {self._model_divergence:.4f}",
                f"LR={self._lr_pd:.4f} GBM={self._gbm_pd:.4f} NN={self._nn_pd:.4f}",
                f"Ensemble PD={self._ensemble_pd:.4f}",
                f"Expected Loss={self._loss_metrics.get('expected_loss', 0):.2f}",
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
        import json as _json
        app    = state.application_input
        fraud  = state.fraud_result
        credit = state.credit_result
        cap    = credit.payment_capacity if credit else None
        bureau = credit.bureau_data if credit else None

        anomaly_text = "\n".join(
            f"  [{a.severity.value}] {a.description}" for a in verification.anomaly_flags
        ) or "  Ninguna"

        return f"""
═══ RESULTADOS DE MODELOS PREDICTIVOS ═══
Logistic Regression PD: {self._lr_pd:.6f}  (peso ensemble: 30%)
Gradient Boosting PD:   {self._gbm_pd:.6f}  (peso ensemble: 45%)
Neural Network PD:      {self._nn_pd:.6f}  (peso ensemble: 25%)
Ensemble PD (12 meses): {self._ensemble_pd:.6f}
Divergencia máxima:     {self._model_divergence:.6f} {'⚠️ ALTA' if self._model_divergence > _MAX_MODEL_DIVERGENCE else '✓ OK'}

═══ MÉTRICAS BASEL III ═══
Expected Loss:          {self._loss_metrics.get('expected_loss', 0):.4f}
Loss Given Default:     0.45 (sector estándar)
Exposure at Default:    {app.credit_request.requested_amount if app else 0:.2f}
Unexpected Loss (95%):  {self._loss_metrics.get('unexpected_loss_95', 0):.4f}
Risk-Weighted Assets:   {self._loss_metrics.get('risk_weighted_assets', 0):.4f}

═══ SHAP VALUES (top variables por importancia) ═══
{_json.dumps(dict(sorted(self._shap_values.items(), key=lambda x: abs(x[1]), reverse=True)[:8]), indent=2) if self._shap_values else 'No disponibles'}

═══ FEATURE VECTOR (normalizado) ═══
Credit Score (norm):    {self._features.get('credit_score', 0):.4f}
Utilización crédito:    {self._features.get('credit_utilization', 0):.4f}
Cuentas morosas:        {self._features.get('delinquent_accounts', 0):.4f}
Historial pagos:        {self._features.get('payment_history_score', 0):.4f}
DTI post-crédito:       {self._features.get('post_credit_dti', 0):.4f}
Fraud score:            {self._features.get('fraud_score', 0):.4f}
Estabilidad ingresos:   {self._features.get('income_stability', 0):.4f}
Ratio monto/ingreso:    {self._features.get('amount_to_income_ratio', 0):.4f}
Quiebra histórica:      {self._features.get('bankruptcy_history', 0):.0f}
Meses empleo (norm):    {self._features.get('employment_months', 0):.4f}

═══ CONTEXTO DE AGENTES PREVIOS ═══
Fraud Score: {(fraud.fraud_score if fraud else 0.0):.4f} | Nivel: {fraud.risk_level.value if fraud else 'N/A'}
Credit Risk: {credit.overall_credit_risk.value if credit else 'N/A'}
Bureau Score: {bureau.credit_score if bureau else 0} | Quiebra: {bureau.bankruptcy_history if bureau else False}
DTI post-crédito: {(cap.post_credit_dti if cap else 0.0):.4f} | Puede pagar: {cap.can_afford if cap else False}
Cuota máx. pagable: {(cap.max_affordable_installment if cap else 0):.2f}

═══ ANOMALÍAS DE VERIFICACIÓN ═══
{anomaly_text}

SOLICITUD: monto={app.credit_request.requested_amount if app else 0} | plazo={app.credit_request.term_months if app else 0}m

Genera el análisis actuarial en este JSON exacto:
{{
  "composite_score": <float 0.0-1000.0>,
  "score_band": "<AA|A|BBB|BB|B|CCC|D>",
  "score_percentile": <float 0.0-100.0>,
  "risk_category": "<MINIMAL|LOW|MEDIUM|HIGH|CRITICAL>",
  "interest_rate_suggestion": <float % anual — mínimo 5.0, máximo 60.0>,
  "max_recommended_amount": <float — limitado por DTI>,
  "max_recommended_term_months": <int — máximo 84>,
  "risk_drivers": ["<factor negativo>"],
  "mitigating_factors": ["<factor positivo>"],
  "variables_used": ["<var>"],
  "confidence": <float 0.00-1.00>,
  "reasoning_chain": ["<paso1>", "<paso2>"],
  "explanation": "<documentación regulatoria SR 11-7>",
  "decision_support": "<recomendación para agente aprobador>",
  "model_divergence_flag": <true|false>,
  "counterfactual": "<qué habría cambiado el score>",
  "limitations": ["<limitación del modelo>"]
}}
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L7 — REGLAS + CORRECCIÓN
    # ═══════════════════════════════════════════════════════════════════════

    def _check_business_rule_violations(
        self,
        parsed: dict[str, Any],
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> list[str]:
        violations: list[str] = []
        credit = state.credit_result
        cap = credit.payment_capacity if credit else None
        app = state.application_input

        score   = float(parsed.get("composite_score", -1))
        band    = parsed.get("score_band", "")
        risk    = parsed.get("risk_category", "")
        rate    = float(parsed.get("interest_rate_suggestion", 0))
        max_amt = float(parsed.get("max_recommended_amount", 0))

        # R1: composite_score en rango
        if not (0 <= score <= 1000):
            violations.append(f"RANGO INVÁLIDO: composite_score={score} debe estar en [0, 1000]")
            return violations

        # R2: score_band vs composite_score
        expected_band, expected_risk, _ = _composite_to_band(score)
        if band and band != expected_band:
            violations.append(
                f"MISMATCH score↔band: score={score:.1f} → band debería ser {expected_band}, declarado={band}"
            )

        # R3: risk_category vs score_band
        if risk and expected_risk.value != risk:
            violations.append(
                f"MISMATCH band↔risk: band={expected_band} → risk debería ser {expected_risk.value}, declarado={risk}"
            )

        # R4: PD > 0.70 → CRITICAL
        if self._ensemble_pd > 0.70 and risk not in ("HIGH", "CRITICAL"):
            violations.append(
                f"REGLA: PD={self._ensemble_pd:.4f} > 0.70 requiere risk_category=HIGH|CRITICAL"
            )

        # R5: Tasa de interés en rango legal/viable
        if rate < 5.0:
            violations.append(f"INVIABLE: interest_rate_suggestion={rate}% < 5% mínimo")
        elif rate > 60.0:
            violations.append(f"ILEGAL: interest_rate_suggestion={rate}% > 60% máximo legal")

        # R6: max_recommended_amount no puede superar lo que permite el DTI
        if cap and cap.max_affordable_installment > 0 and max_amt > 0:
            monthly_rate = rate / 100 / 12 if rate > 0 else 0.15 / 12
            term = int(parsed.get("max_recommended_term_months", 36))
            if monthly_rate > 0 and term > 0:
                max_viable = cap.max_affordable_installment * (1 - (1 + monthly_rate) ** (-term)) / monthly_rate
                if max_amt > max_viable * 1.1:
                    violations.append(
                        f"EXCEDE_DTI: max_recommended_amount={max_amt:.2f} > viable={max_viable:.2f} dado DTI"
                    )

        # R7: confidence presente
        conf = parsed.get("confidence")
        if conf is None or not (0.0 <= float(conf) <= 1.0):
            violations.append("CAMPO FALTANTE o INVÁLIDO: 'confidence'")

        return violations

    def _build_correction_prompt(
        self,
        original: dict[str, Any],
        violations: list[str],
        tool_results: ToolExecutionResults,
    ) -> str:
        return f"""
Tu análisis actuarial tiene violaciones que corregir:

VIOLACIONES:
{chr(10).join(f'  {i+1}. {v}' for i, v in enumerate(violations))}

DATOS OBJETIVOS (no modificar):
- Ensemble PD: {self._ensemble_pd:.6f}
- Score actuarial correcto para PD={self._ensemble_pd:.4f}: {_pd_to_composite_score(self._ensemble_pd):.1f}
- Banda correcta: {_composite_to_band(_pd_to_composite_score(self._ensemble_pd))[0]}
- Risk category correcta: {_composite_to_band(_pd_to_composite_score(self._ensemble_pd))[1].value}

ANÁLISIS ANTERIOR:
{original}

Corrige SOLO los campos con violaciones. El JSON corregido debe ser autocontenido.
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
        # Calidad del ensemble: mejor cuando los modelos coinciden
        agreement = max(0.0, 1.0 - self._model_divergence / _MAX_MODEL_DIVERGENCE)

        return [
            QualityDimension(
                name="model_agreement",
                description="Acuerdo entre los 3 modelos predictivos",
                score=min(1.0, agreement),
                weight=0.35,
                flags=[f"DIVERGENCIA={self._model_divergence:.4f}"] if self._model_divergence > 0.15 else [],
            ),
            QualityDimension(
                name="feature_completeness",
                description="Completitud del feature vector para los modelos",
                score=min(1.0, tool_results.overall_success_rate + 0.2),
                weight=0.25,
                flags=["FEATURES_COMPLETOS" if tool_results.overall_success_rate > 0.8 else "FEATURES_PARCIALES"],
            ),
            QualityDimension(
                name="shap_explainability",
                description="Disponibilidad de explicabilidad SHAP",
                score=0.9 if self._shap_values else 0.3,
                weight=0.20,
                flags=[] if self._shap_values else ["SHAP_NO_DISPONIBLE"],
            ),
            QualityDimension(
                name="analysis_convergence",
                description="Convergencia del análisis (correcciones necesarias)",
                score=corrected.convergence_score,
                weight=0.20,
                flags=["MAX_CORRECCIONES_ALCANZADO"] if corrected.max_iterations_reached else [],
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
        app  = state.application_input

        composite = float(data.get("composite_score", _pd_to_composite_score(self._ensemble_pd)))
        band, risk_cat, rate_range = _composite_to_band(composite)

        predictive_model = PredictiveModelOutput(
            model_name="CloudBank-EnsembleRisk-DeepAgent-v2",
            model_version="2.1.0",
            default_probability_12m=self._ensemble_pd,
            default_probability_24m=min(1.0, self._ensemble_pd * 1.28),
            default_probability_36m=min(1.0, self._ensemble_pd * 1.50),
            expected_loss=float(self._loss_metrics.get("expected_loss", 0)),
            loss_given_default=0.45,
            exposure_at_default=app.credit_request.requested_amount if app else 0,
            feature_importance={k: float(v) for k, v in self._shap_values.items()},
            shap_values={k: float(v) for k, v in self._shap_values.items()},
            confidence_interval_lower=max(0.0, self._ensemble_pd - 0.04),
            confidence_interval_upper=min(1.0, self._ensemble_pd + 0.04),
        )

        actuarial_score_obj = ActuarialScore(
            composite_score=composite,
            score_band=data.get("score_band", band),
            score_percentile=float(data.get("score_percentile", (composite / 1000) * 100)),
            risk_category=RiskLevel(data.get("risk_category", risk_cat.value)),
            interest_rate_suggestion=float(data.get("interest_rate_suggestion", rate_range[0])),
            max_recommended_amount=float(data.get("max_recommended_amount", 0)),
            max_recommended_term_months=int(data.get("max_recommended_term_months", 36)),
        )

        actuarial_result = ActuarialResult(
            status=AgentStatus.SUCCESS,
            predictive_model=predictive_model,
            actuarial_score=actuarial_score_obj,
            risk_drivers=data.get("risk_drivers", []),
            mitigating_factors=data.get("mitigating_factors", []),
            variables_used=data.get("variables_used", list(self._features.keys())),
            explanation=justification.model_documentation,
            decision_support=data.get("decision_support", ""),
            execution_time_ms=metrics.total_duration_ms,
            retry_count=state.get_retry_count(self.agent_name),
        )

        updated = state.model_copy(update={
            "actuarial_result": actuarial_result,
            "current_node": self.agent_name,
        })
        return updated.add_audit_event(
            node=self.agent_name,
            action="ACTUARIAL_DEEP_ANALYSIS_COMPLETE",
            actor=self.agent_name,
            outcome=f"pd={self._ensemble_pd:.6f} score={composite:.1f} band={data.get('score_band', band)} risk={data.get('risk_category', risk_cat.value)}",
            duration_ms=metrics.total_duration_ms,
            metadata={
                "confidence": metrics.confidence_score,
                "quality": metrics.quality_score,
                "model_divergence": self._model_divergence,
                "expected_loss": self._loss_metrics.get("expected_loss", 0),
                "corrections": metrics.self_corrections_count,
                "shap_available": bool(self._shap_values),
            },
        )
