"""
CLOUD BANK — Esquema Maestro de Estado LangGraph
Define el estado compartido entre todos los agentes del grafo.
Todas las operaciones son inmutables: los agentes solo agregan, nunca sobrescriben.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Optional
from pydantic import BaseModel, Field, field_validator
from langgraph.graph.message import add_messages


# ─── Enumeraciones de Dominio ─────────────────────────────────────────────────

class ApplicationStatus(str, Enum):
    PENDING        = "PENDING"
    IN_REVIEW      = "IN_REVIEW"
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"
    MORE_DOCS      = "MORE_DOCS_REQUIRED"
    ESCALATED      = "ESCALATED_TO_COMMITTEE"
    BLOCKED        = "BLOCKED_FRAUD"
    ERROR          = "ERROR"


class RiskLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    MINIMAL  = "MINIMAL"


class DecisionReason(str, Enum):
    APPROVED_GOOD_PROFILE      = "APPROVED_GOOD_PROFILE"
    APPROVED_ACCEPTABLE_RISK   = "APPROVED_ACCEPTABLE_RISK"
    REJECTED_FRAUD             = "REJECTED_FRAUD"
    REJECTED_CREDIT_HISTORY    = "REJECTED_CREDIT_HISTORY"
    REJECTED_INSUFFICIENT_CAPACITY = "REJECTED_INSUFFICIENT_CAPACITY"
    REJECTED_HIGH_DEFAULT_RISK = "REJECTED_HIGH_DEFAULT_RISK"
    MORE_DOCS_IDENTITY         = "MORE_DOCS_IDENTITY"
    MORE_DOCS_INCOME           = "MORE_DOCS_INCOME"
    ESCALATED_BORDERLINE       = "ESCALATED_BORDERLINE"
    ESCALATED_HIGH_AMOUNT      = "ESCALATED_HIGH_AMOUNT"
    ESCALATED_POLICY_EXCEPTION = "ESCALATED_POLICY_EXCEPTION"


class AgentStatus(str, Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    SUCCESS   = "SUCCESS"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"
    RETRYING  = "RETRYING"


# ─── Contexto de Seguridad ────────────────────────────────────────────────────

class SecurityContext(BaseModel):
    authenticated: bool = False
    principal_id: str = ""
    channel: str = ""
    ip_address: str = ""
    device_fingerprint: str = ""
    user_agent: str = ""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_signature: str = ""
    tls_version: str = ""
    geo_country: str = ""
    geo_city: str = ""
    is_vpn: bool = False
    is_tor: bool = False
    is_datacenter_ip: bool = False
    threat_level: RiskLevel = RiskLevel.MINIMAL
    security_flags: list[str] = Field(default_factory=list)


# ─── Evento de Auditoría ──────────────────────────────────────────────────────

class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    node: str
    action: str
    actor: str
    outcome: str
    duration_ms: Optional[float] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    pii_accessed: bool = False
    data_classification: str = "CONFIDENTIAL"


# ─── Salida del Agente Antifraude ─────────────────────────────────────────────

class DocumentVerification(BaseModel):
    document_type: str
    is_authentic: bool
    confidence: float = Field(ge=0.0, le=1.0)
    tamper_indicators: list[str] = Field(default_factory=list)
    ocr_consistency_score: float = Field(ge=0.0, le=1.0)
    metadata_integrity: bool = True


class BiometricAnalysis(BaseModel):
    liveness_score: float = Field(ge=0.0, le=1.0)
    face_match_score: float = Field(ge=0.0, le=1.0)
    deepfake_probability: float = Field(ge=0.0, le=1.0)
    spoofing_detected: bool = False
    biometric_flags: list[str] = Field(default_factory=list)


class BehavioralSignals(BaseModel):
    typing_pattern_anomaly: float = Field(ge=0.0, le=1.0, default=0.0)
    navigation_pattern_anomaly: float = Field(ge=0.0, le=1.0, default=0.0)
    time_on_fields_anomaly: float = Field(ge=0.0, le=1.0, default=0.0)
    copy_paste_detected: bool = False
    auto_fill_detected: bool = False
    bot_probability: float = Field(ge=0.0, le=1.0, default=0.0)


class DeviceIntelligence(BaseModel):
    device_id: str = ""
    is_emulator: bool = False
    is_rooted: bool = False
    device_reputation_score: float = Field(ge=0.0, le=1.0, default=0.5)
    previous_fraud_associations: int = 0
    device_flags: list[str] = Field(default_factory=list)


class IPIntelligence(BaseModel):
    ip_address: str = ""
    reputation_score: float = Field(ge=0.0, le=1.0, default=0.5)
    is_proxy: bool = False
    is_vpn: bool = False
    is_tor: bool = False
    is_datacenter: bool = False
    country: str = ""
    previous_fraud_count: int = 0
    ip_flags: list[str] = Field(default_factory=list)


class FraudAnalysisResult(BaseModel):
    status: AgentStatus = AgentStatus.PENDING
    fraud_score: float = Field(ge=0.0, le=1.0, default=0.0)
    risk_level: RiskLevel = RiskLevel.MINIMAL
    is_blocked: bool = False
    document_verification: Optional[DocumentVerification] = None
    biometric_analysis: Optional[BiometricAnalysis] = None
    behavioral_signals: Optional[BehavioralSignals] = None
    device_intelligence: Optional[DeviceIntelligence] = None
    ip_intelligence: Optional[IPIntelligence] = None
    fraud_flags: list[str] = Field(default_factory=list)
    explanation: str = ""
    recommendation: str = ""
    contributing_factors: dict[str, float] = Field(default_factory=dict)
    execution_time_ms: float = 0.0
    model_version: str = ""
    error: Optional[str] = None
    retry_count: int = 0


# ─── Salida del Agente Historial Crediticio ───────────────────────────────────

class CreditBureauData(BaseModel):
    bureau_name: str
    credit_score: int = Field(ge=0, le=1000)
    score_model: str
    total_accounts: int = 0
    open_accounts: int = 0
    delinquent_accounts: int = 0
    total_debt: float = 0.0
    credit_utilization: float = Field(ge=0.0, le=1.0, default=0.0)
    oldest_account_months: int = 0
    payment_history_score: float = Field(ge=0.0, le=1.0, default=0.0)
    negative_marks: list[str] = Field(default_factory=list)
    bankruptcy_history: bool = False
    query_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IncomeAnalysis(BaseModel):
    declared_monthly_income: float
    verified_monthly_income: float
    income_verification_method: str
    income_stability_score: float = Field(ge=0.0, le=1.0)
    income_source: str
    employment_type: str
    employment_duration_months: int
    income_consistency: bool = True
    income_discrepancy_flag: bool = False


class ExpenseAnalysis(BaseModel):
    total_monthly_obligations: float
    rent_or_mortgage: float = 0.0
    existing_loans: float = 0.0
    credit_card_minimums: float = 0.0
    other_obligations: float = 0.0
    declared_vs_bureau_discrepancy: float = 0.0


class PaymentCapacity(BaseModel):
    gross_monthly_income: float
    total_monthly_obligations: float
    disposable_income: float
    debt_to_income_ratio: float
    requested_installment: float
    post_credit_dti: float
    capacity_score: float = Field(ge=0.0, le=1.0)
    can_afford: bool = False
    max_affordable_installment: float = 0.0


class CreditHistoryResult(BaseModel):
    status: AgentStatus = AgentStatus.PENDING
    bureau_data: Optional[CreditBureauData] = None
    income_analysis: Optional[IncomeAnalysis] = None
    expense_analysis: Optional[ExpenseAnalysis] = None
    payment_capacity: Optional[PaymentCapacity] = None
    aml_clear: bool = True
    aml_flags: list[str] = Field(default_factory=list)
    information_consistent: bool = True
    inconsistency_flags: list[str] = Field(default_factory=list)
    overall_credit_risk: RiskLevel = RiskLevel.MEDIUM
    explanation: str = ""
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    retry_count: int = 0


# ─── Salida del Agente Actuario ───────────────────────────────────────────────

class PredictiveModelOutput(BaseModel):
    model_name: str
    model_version: str
    default_probability_12m: float = Field(ge=0.0, le=1.0)
    default_probability_24m: float = Field(ge=0.0, le=1.0)
    default_probability_36m: float = Field(ge=0.0, le=1.0)
    expected_loss: float = 0.0
    loss_given_default: float = 0.0
    exposure_at_default: float = 0.0
    feature_importance: dict[str, float] = Field(default_factory=dict)
    shap_values: dict[str, float] = Field(default_factory=dict)
    confidence_interval_lower: float = 0.0
    confidence_interval_upper: float = 0.0


class ActuarialScore(BaseModel):
    composite_score: float = Field(ge=0.0, le=1000.0)
    score_band: str = ""
    score_percentile: float = Field(ge=0.0, le=100.0, default=0.0)
    risk_category: RiskLevel = RiskLevel.MEDIUM
    interest_rate_suggestion: float = 0.0
    max_recommended_amount: float = 0.0
    max_recommended_term_months: int = 0


class ActuarialResult(BaseModel):
    status: AgentStatus = AgentStatus.PENDING
    predictive_model: Optional[PredictiveModelOutput] = None
    actuarial_score: Optional[ActuarialScore] = None
    risk_drivers: list[str] = Field(default_factory=list)
    mitigating_factors: list[str] = Field(default_factory=list)
    variables_used: list[str] = Field(default_factory=list)
    explanation: str = ""
    decision_support: str = ""
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    retry_count: int = 0


# ─── Salida del Agente Aprobador ──────────────────────────────────────────────

class ApprovalCondition(BaseModel):
    condition_type: str
    description: str
    required: bool = True
    deadline_days: Optional[int] = None


class CreditTerms(BaseModel):
    approved_amount: float
    interest_rate_annual: float
    term_months: int
    monthly_installment: float
    total_cost: float
    opening_fee: float = 0.0
    insurance_required: bool = False
    collateral_required: bool = False
    conditions: list[ApprovalCondition] = Field(default_factory=list)


class EscalationPackage(BaseModel):
    escalation_reason: str
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
    committee_type: str
    summary: str
    fraud_score: float
    credit_score: int
    default_probability: float
    requested_amount: float
    recommended_decision: str
    key_concerns: list[str] = Field(default_factory=list)
    supporting_data: dict[str, Any] = Field(default_factory=dict)


class ApprovalResult(BaseModel):
    status: AgentStatus = AgentStatus.PENDING
    decision: Optional[ApplicationStatus] = None
    decision_reason: Optional[DecisionReason] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    credit_terms: Optional[CreditTerms] = None
    rejection_reasons: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    escalation_package: Optional[EscalationPackage] = None
    justification: str = ""
    regulatory_basis: str = ""
    decision_factors: dict[str, str] = Field(default_factory=dict)
    human_review_required: bool = False
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    retry_count: int = 0


# ─── Datos de la Solicitud (Input) ────────────────────────────────────────────

class ApplicantIdentity(BaseModel):
    national_id: str
    id_type: str
    full_name: str
    date_of_birth: str
    nationality: str
    tax_id: Optional[str] = None

    @field_validator("national_id", "full_name")
    @classmethod
    def sanitize_string(cls, v: str) -> str:
        return v.strip()


class ApplicantContact(BaseModel):
    email: str
    phone: str
    address: str
    city: str
    country: str
    postal_code: str


class CreditRequest(BaseModel):
    requested_amount: float = Field(gt=0, le=500_000)
    term_months: int = Field(ge=6, le=84)
    purpose: str
    currency: str = "USD"

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: str) -> str:
        allowed = {
            "personal", "home_improvement", "medical", "education",
            "vehicle", "debt_consolidation", "business", "travel", "other"
        }
        if v.lower() not in allowed:
            raise ValueError(f"Propósito inválido. Permitidos: {allowed}")
        return v.lower()


class ApplicationInput(BaseModel):
    identity: ApplicantIdentity
    contact: ApplicantContact
    credit_request: CreditRequest
    monthly_income: float = Field(gt=0)
    employment_type: str
    employer_name: Optional[str] = None
    employment_months: int = Field(ge=0)
    additional_income: float = Field(ge=0, default=0.0)
    monthly_obligations: float = Field(ge=0, default=0.0)
    document_references: list[str] = Field(default_factory=list)
    biometric_token: Optional[str] = None
    device_fingerprint: Optional[str] = None
    channel: str = "web"
    ip_address: str = ""
    user_agent: str = ""
    consent_given: bool = Field(default=False)

    @field_validator("consent_given")
    @classmethod
    def must_have_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Se requiere consentimiento explícito para procesar la solicitud")
        return v


# ─── Estado Maestro del Grafo ─────────────────────────────────────────────────

from typing import Literal

class CreditEvaluationState(BaseModel):
    """
    Estado compartido e inmutable entre todos los nodos del grafo LangGraph.
    Cada agente lee el estado completo y agrega su resultado sin modificar los anteriores.
    """

    # ── Metadatos de la solicitud ──
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    application_version: str = "2.0"

    # ── Datos de entrada (encriptados en reposo) ──
    application_input: Optional[ApplicationInput] = None
    input_hash: str = ""

    # ── Contexto de seguridad ──
    security_context: SecurityContext = Field(default_factory=SecurityContext)

    # ── Control de flujo ──
    current_node: str = "START"
    status: ApplicationStatus = ApplicationStatus.PENDING
    flow_flags: dict[str, bool] = Field(default_factory=dict)

    # ── Reintentos por nodo ──
    retry_counts: dict[str, int] = Field(default_factory=dict)
    max_retries: int = 3

    # ── Resultados de agentes ──
    fraud_result: Optional[FraudAnalysisResult] = None
    credit_result: Optional[CreditHistoryResult] = None
    actuarial_result: Optional[ActuarialResult] = None
    approval_result: Optional[ApprovalResult] = None

    # ── Errores y recuperación ──
    errors: list[dict[str, Any]] = Field(default_factory=list)
    is_fatal_error: bool = False
    error_node: Optional[str] = None

    # ── Auditoría completa ──
    audit_trail: list[AuditEvent] = Field(default_factory=list)

    # ── Mensajes LangGraph (para memoria de conversación de agentes) ──
    messages: Annotated[list, add_messages] = Field(default_factory=list)

    # ── Métricas de rendimiento ──
    total_duration_ms: float = 0.0
    node_durations: dict[str, float] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def add_audit_event(self, node: str, action: str, actor: str, outcome: str, **kwargs) -> "CreditEvaluationState":
        event = AuditEvent(
            node=node,
            action=action,
            actor=actor,
            outcome=outcome,
            **kwargs,
        )
        return self.model_copy(update={"audit_trail": [*self.audit_trail, event]})

    def add_error(self, node: str, error: str, is_fatal: bool = False) -> "CreditEvaluationState":
        error_entry = {
            "node": node,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_fatal": is_fatal,
        }
        return self.model_copy(update={
            "errors": [*self.errors, error_entry],
            "is_fatal_error": is_fatal,
            "error_node": node if is_fatal else self.error_node,
        })

    def increment_retry(self, node: str) -> "CreditEvaluationState":
        new_counts = dict(self.retry_counts)
        new_counts[node] = new_counts.get(node, 0) + 1
        return self.model_copy(update={"retry_counts": new_counts})

    def get_retry_count(self, node: str) -> int:
        return self.retry_counts.get(node, 0)

    def can_retry(self, node: str) -> bool:
        return self.get_retry_count(node) < self.max_retries
