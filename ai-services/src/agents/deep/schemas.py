"""
CLOUD BANK — Esquemas Internos de los Deep Agents
Define los contratos tipados entre cada capa del pipeline de razonamiento.
Cada capa produce un resultado tipado que la siguiente capa consume.
Ningún estado se pasa como dict libre: todo es Pydantic con validación estricta.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════════
# ENUMERACIONES
# ═══════════════════════════════════════════════════════════════════════════

class LayerStatus(str, Enum):
    SUCCESS   = "SUCCESS"
    DEGRADED  = "DEGRADED"  # Completó con datos parciales
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"


class InconsistencySeverity(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class CorrectionTrigger(str, Enum):
    BUSINESS_RULE_VIOLATION   = "BUSINESS_RULE_VIOLATION"
    DATA_INCONSISTENCY        = "DATA_INCONSISTENCY"
    RANGE_VIOLATION           = "RANGE_VIOLATION"
    LOGICAL_CONTRADICTION     = "LOGICAL_CONTRADICTION"
    HALLUCINATION_DETECTED    = "HALLUCINATION_DETECTED"
    MISSING_REQUIRED_FIELD    = "MISSING_REQUIRED_FIELD"
    SCORE_LEVEL_MISMATCH      = "SCORE_LEVEL_MISMATCH"


class PlanStrategy(str, Enum):
    FULL_PARALLEL     = "FULL_PARALLEL"      # Todas las herramientas en paralelo
    GROUPED_PARALLEL  = "GROUPED_PARALLEL"   # Grupos paralelos con dependencias
    SEQUENTIAL        = "SEQUENTIAL"         # Una a la vez
    ADAPTIVE          = "ADAPTIVE"           # Decide en runtime según datos


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 1 — VALIDACIÓN DE ENTRADA
# ═══════════════════════════════════════════════════════════════════════════

class InputValidationResult(BaseModel):
    """Salida de la Capa 1: Validación de Entrada."""
    layer: str = "L1_INPUT_VALIDATION"
    status: LayerStatus = LayerStatus.SUCCESS
    validated_data: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    injection_detected: bool = False
    jailbreak_detected: bool = False
    poisoning_detected: bool = False
    data_completeness_score: float = Field(ge=0.0, le=1.0, default=1.0)
    required_fields_present: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    sanitization_applied: bool = False
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 2 — VALIDACIÓN DE CONTEXTO
# ═══════════════════════════════════════════════════════════════════════════

class PriorAgentResultValidation(BaseModel):
    agent_name: str
    result_present: bool
    result_status: str
    is_usable: bool
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    warnings: list[str] = Field(default_factory=list)


class ContextValidationResult(BaseModel):
    """Salida de la Capa 2: Validación de Contexto."""
    layer: str = "L2_CONTEXT_VALIDATION"
    status: LayerStatus = LayerStatus.SUCCESS
    is_valid: bool = True
    prior_results: list[PriorAgentResultValidation] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    context_quality_score: float = Field(ge=0.0, le=1.0, default=1.0)
    cross_agent_inconsistencies: list[str] = Field(default_factory=list)
    state_integrity_valid: bool = True
    security_context_valid: bool = True
    warnings: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 3 — PLANIFICACIÓN
# ═══════════════════════════════════════════════════════════════════════════

class ToolInvocationPlan(BaseModel):
    tool_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    tool_name: str
    parallel_group: int  # 0 = primero, 1 = segundo, etc.
    is_required: bool = True
    is_available: bool = True
    skip_reason: str = ""
    timeout_s: float = 10.0
    expected_output_type: str = ""
    fallback_strategy: str = "USE_DEGRADED_RESULT"
    input_params: dict[str, Any] = Field(default_factory=dict)


class AgentPlan(BaseModel):
    """Salida de la Capa 3: Plan de Ejecución."""
    layer: str = "L3_PLANNING"
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy: PlanStrategy = PlanStrategy.FULL_PARALLEL
    tool_invocations: list[ToolInvocationPlan] = Field(default_factory=list)
    total_tools: int = 0
    skipped_tools: int = 0
    parallel_groups_count: int = 1
    critical_path: list[str] = Field(default_factory=list)
    contingencies: dict[str, str] = Field(default_factory=dict)
    estimated_duration_s: float = 0.0
    execution_notes: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 4 — EJECUCIÓN DE HERRAMIENTAS
# ═══════════════════════════════════════════════════════════════════════════

class ToolResult(BaseModel):
    tool_name: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    is_degraded: bool = False
    degradation_reason: str = ""
    retry_count: int = 0
    data_quality: float = Field(ge=0.0, le=1.0, default=1.0)


class ToolExecutionResults(BaseModel):
    """Salida de la Capa 4: Resultados de Herramientas."""
    layer: str = "L4_TOOL_EXECUTION"
    status: LayerStatus = LayerStatus.SUCCESS
    results: dict[str, ToolResult] = Field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    degraded_count: int = 0
    overall_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    total_duration_ms: float = 0.0
    critical_tool_failed: bool = False
    partial_data_available: bool = True

    @property
    def tool_count(self) -> int:
        return len(self.results)


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 5 — VERIFICACIÓN
# ═══════════════════════════════════════════════════════════════════════════

class Inconsistency(BaseModel):
    inconsistency_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    field_a: str
    field_b: str
    expected_relationship: str
    actual_values: str
    severity: InconsistencySeverity = InconsistencySeverity.MEDIUM
    description: str
    can_auto_correct: bool = False
    correction_suggestion: str = ""


class AnomalyFlag(BaseModel):
    flag_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: str
    description: str
    severity: InconsistencySeverity = InconsistencySeverity.LOW
    data_point: str = ""
    expected_range: str = ""
    actual_value: str = ""


class VerificationResult(BaseModel):
    """Salida de la Capa 5: Verificación Cruzada."""
    layer: str = "L5_VERIFICATION"
    status: LayerStatus = LayerStatus.SUCCESS
    is_consistent: bool = True
    inconsistencies: list[Inconsistency] = Field(default_factory=list)
    anomaly_flags: list[AnomalyFlag] = Field(default_factory=list)
    cross_validation_passed: bool = True
    data_quality_score: float = Field(ge=0.0, le=1.0, default=1.0)
    critical_anomalies_count: int = 0
    verification_notes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def has_critical_anomalies(self) -> bool:
        return self.critical_anomalies_count > 0 or any(
            i.severity == InconsistencySeverity.CRITICAL for i in self.inconsistencies
        )


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 6 — RAZONAMIENTO LLM
# ═══════════════════════════════════════════════════════════════════════════

class ReasoningStep(BaseModel):
    step_number: int
    premise: str
    inference: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)


class ReasoningOutput(BaseModel):
    """Salida de la Capa 6: Razonamiento LLM."""
    layer: str = "L6_REASONING"
    status: LayerStatus = LayerStatus.SUCCESS
    raw_llm_response: str = ""
    parsed_data: dict[str, Any] = Field(default_factory=dict)
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    tokens_input: int = 0
    tokens_output: int = 0
    model_used: str = ""
    parse_errors: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 7 — AUTOCORRECCIÓN
# ═══════════════════════════════════════════════════════════════════════════

class Correction(BaseModel):
    correction_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    field: str
    original_value: Any
    corrected_value: Any
    trigger: CorrectionTrigger
    rule_violated: str = ""
    confidence_impact: float = 0.0  # Positivo = mejora, negativo = degrada
    description: str = ""


class SelfCorrectionResult(BaseModel):
    """Salida de la Capa 7: Autocorrección."""
    layer: str = "L7_SELF_CORRECTION"
    status: LayerStatus = LayerStatus.SUCCESS
    iterations_performed: int = 0
    max_iterations: int = 3
    corrections: list[Correction] = Field(default_factory=list)
    final_data: dict[str, Any] = Field(default_factory=dict)
    original_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    final_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    correction_needed: bool = False
    max_iterations_reached: bool = False
    convergence_score: float = Field(ge=0.0, le=1.0, default=1.0)
    duration_ms: float = 0.0

    @property
    def corrections_count(self) -> int:
        return len(self.corrections)

    @property
    def confidence_delta(self) -> float:
        return self.final_confidence - self.original_confidence


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 8 — EVALUACIÓN DE CALIDAD
# ═══════════════════════════════════════════════════════════════════════════

class QualityDimension(BaseModel):
    name: str
    description: str
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)


class QualityAssessment(BaseModel):
    """Salida de la Capa 8: Evaluación de Calidad."""
    layer: str = "L8_QUALITY_ASSESSMENT"
    status: LayerStatus = LayerStatus.SUCCESS

    # Scores principales
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    risk_score: float = Field(ge=0.0, le=1.0, default=0.5)
    quality_score: float = Field(ge=0.0, le=1.0, default=0.5)

    # Dimensiones de calidad
    dimensions: list[QualityDimension] = Field(default_factory=list)

    # Flags
    is_reliable: bool = True
    requires_human_review: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    reliability_concerns: list[str] = Field(default_factory=list)

    # Umbrales
    confidence_threshold_met: bool = True
    minimum_quality_threshold_met: bool = True

    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 9 — JUSTIFICACIÓN
# ═══════════════════════════════════════════════════════════════════════════

class RegulatoryReference(BaseModel):
    regulation: str
    article: str
    description: str
    compliance_status: str = "COMPLIANT"


class Justification(BaseModel):
    """Salida de la Capa 9: Justificación Regulatoria."""
    layer: str = "L9_JUSTIFICATION"
    status: LayerStatus = LayerStatus.SUCCESS

    executive_summary: str = ""
    reasoning_chain: list[str] = Field(default_factory=list)
    factors_considered: dict[str, str] = Field(default_factory=dict)
    factors_weighted: dict[str, float] = Field(default_factory=dict)
    regulatory_references: list[RegulatoryReference] = Field(default_factory=list)

    # GDPR Article 22 — right to explanation
    gdpr_explanation: str = ""
    # Basel III / SR 11-7 model documentation
    model_documentation: str = ""

    counterfactual: str = ""  # "¿Qué habría cambiado la decisión?"
    limitations_disclosed: list[str] = Field(default_factory=list)

    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MÉTRICAS GLOBALES DEL DEEP AGENT
# ═══════════════════════════════════════════════════════════════════════════

class DeepAgentMetrics(BaseModel):
    agent_name: str
    request_id: str
    pipeline_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    total_duration_ms: float = 0.0

    # Por capa
    layer_durations: dict[str, float] = Field(default_factory=dict)
    layer_statuses: dict[str, str] = Field(default_factory=dict)

    # Calidad
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.0)
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    risk_score: float = Field(ge=0.0, le=1.0, default=0.0)

    # Operaciones
    tool_calls_total: int = 0
    tool_success_count: int = 0
    tool_failure_count: int = 0
    tool_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    self_corrections_count: int = 0
    llm_calls_count: int = 0
    llm_tokens_total: int = 0

    # Seguridad
    security_events: int = 0
    injection_attempts: int = 0
    anomalies_detected: int = 0

    def finalize(self) -> "DeepAgentMetrics":
        self.completed_at = datetime.now(timezone.utc)
        if self.tool_calls_total > 0:
            self.tool_success_rate = self.tool_success_count / self.tool_calls_total
        return self
