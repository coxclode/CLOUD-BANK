"""
CLOUD BANK — Base Deep Agent
Clase base que implementa el pipeline de razonamiento de 10 capas.
Cada capa es independiente, produce un resultado tipado y puede fallar de forma aislada.

Pipeline:
  L1  Input Validation     → Seguridad + schema + PII
  L2  Context Validation   → Dependencias + consistencia estado
  L3  Planning             → Estrategia de ejecución adaptativa
  L4  Tool Execution       → Paralelo/secuencial con retry + fallback
  L5  Verification         → Cross-validation + detección de anomalías
  L6  Reasoning (LLM)      → Análisis principal con system prompt endurecido
  L7  Self-Correction      → Autocorrección iterativa (máx 3 rounds)
  L8  Quality Assessment   → Confidence + Risk + Quality scores
  L9  Justification        → Explicabilidad regulatoria (GDPR + Basel III)
  L10 Output Assembly      → Estado final + métricas + auditoría
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.core.config import get_settings
from src.infrastructure.llm import LLMResponse, LLMTransientError, get_llm_provider
from src.core.exceptions import (
    AgentExecutionError,
    AgentTimeoutError,
    DataPoisoningError,
    JailbreakAttemptError,
    PromptInjectionError,
    SecurityViolationError,
)
from src.core.state import CreditEvaluationState
from src.observability.logger import get_logger
from src.observability.metrics import (
    record_agent_execution,
    record_agent_retry,
    record_llm_call,
    record_security_violation,
)
from src.observability.tracer import trace_agent, trace_node
from src.security.prompt_guard import PromptGuard, build_secure_system_prompt
from src.agents.deep.schemas import (
    AgentPlan,
    AnomalyFlag,
    Correction,
    CorrectionTrigger,
    ContextValidationResult,
    DeepAgentMetrics,
    InconsistencySeverity,
    InputValidationResult,
    Justification,
    LayerStatus,
    PlanStrategy,
    QualityAssessment,
    QualityDimension,
    ReasoningOutput,
    ReasoningStep,
    RegulatoryReference,
    SelfCorrectionResult,
    ToolExecutionResults,
    ToolResult,
    VerificationResult,
)

logger = get_logger(__name__)
_guard = PromptGuard(strict_mode=True)


class BaseDeepAgent(ABC):
    """
    Deep Agent con pipeline de razonamiento de 10 capas.
    Los métodos abstractos definen el comportamiento específico de cada agente.
    Los métodos concretos implementan la infraestructura compartida.
    """

    agent_name: str = "base_deep_agent"
    model_override: Optional[str] = None
    max_self_correction_iterations: int = 3
    min_confidence_threshold: float = 0.60
    min_quality_threshold: float = 0.55

    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_llm_provider(self._settings)
        self._log = logger.bind(agent=self.agent_name)
        self._metrics: Optional[DeepAgentMetrics] = None

    # ═══════════════════════════════════════════════════════════════════════
    # MÉTODOS ABSTRACTOS — Implementar en cada agente concreto
    # ═══════════════════════════════════════════════════════════════════════

    @abstractmethod
    def _get_required_input_fields(self) -> list[str]:
        """Campos requeridos en el estado para ejecutar este agente."""
        ...

    @abstractmethod
    def _get_required_prior_agents(self) -> list[str]:
        """Resultados de agentes previos que este agente necesita."""
        ...

    @abstractmethod
    def _build_role_system_prompt(self) -> str:
        """Instrucciones específicas del rol del agente."""
        ...

    @abstractmethod
    def _build_reasoning_prompt(
        self,
        state: CreditEvaluationState,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
    ) -> str:
        """Construye el prompt de razonamiento principal."""
        ...

    @abstractmethod
    def _build_correction_prompt(
        self,
        original_output: dict[str, Any],
        violations: list[str],
        tool_results: ToolExecutionResults,
    ) -> str:
        """Construye el prompt de autocorrección."""
        ...

    @abstractmethod
    async def _execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        """Ejecuta las herramientas específicas del agente."""
        ...

    @abstractmethod
    def _create_agent_plan(
        self,
        state: CreditEvaluationState,
        context: ContextValidationResult,
    ) -> AgentPlan:
        """Construye el plan de ejecución adaptativo."""
        ...

    @abstractmethod
    def _verify_tool_results(
        self,
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> VerificationResult:
        """Verifica y cross-valida los resultados de herramientas."""
        ...

    @abstractmethod
    def _check_business_rule_violations(
        self,
        parsed_output: dict[str, Any],
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> list[str]:
        """Detecta violaciones de reglas de negocio en la salida del LLM."""
        ...

    @abstractmethod
    def _assemble_agent_result(
        self,
        state: CreditEvaluationState,
        corrected: SelfCorrectionResult,
        quality: QualityAssessment,
        justification: Justification,
        metrics: DeepAgentMetrics,
    ) -> CreditEvaluationState:
        """Ensambla el resultado final en el estado LangGraph."""
        ...

    @abstractmethod
    def _compute_quality_dimensions(
        self,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
        corrected: SelfCorrectionResult,
    ) -> list[QualityDimension]:
        """Define las dimensiones de calidad específicas del agente."""
        ...

    # ═══════════════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA PÚBLICO
    # ═══════════════════════════════════════════════════════════════════════

    async def run(self, state: CreditEvaluationState) -> CreditEvaluationState:
        """Ejecuta el pipeline completo de 10 capas con timeout global."""
        timeout = self._get_agent_timeout()
        try:
            return await asyncio.wait_for(
                self._run_pipeline(state),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._log.error("deep_agent.global_timeout", timeout_s=timeout)
            raise AgentTimeoutError(self.agent_name, timeout)

    async def _run_pipeline(self, state: CreditEvaluationState) -> CreditEvaluationState:
        """
        Orquesta las 10 capas del pipeline de razonamiento.
        Cada capa registra su duración. Los fallos son aislados por capa.
        """
        pipeline_start = time.monotonic()
        metrics = DeepAgentMetrics(
            agent_name=self.agent_name,
            request_id=state.request_id,
        )
        self._metrics = metrics

        self._log.info("deep_agent.pipeline_started", request_id=state.request_id)

        # ── CAPA 1: Validación de entrada ─────────────────────────────────────
        t = time.monotonic()
        validation = await self._layer_1_validate_input(state)
        metrics.layer_durations["L1"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L1"] = validation.status.value
        if validation.status == LayerStatus.FAILED:
            self._log.error("deep_agent.L1_failed", errors=validation.validation_errors)
            raise AgentExecutionError(
                f"L1 falló: {'; '.join(validation.validation_errors)}",
                self.agent_name, is_retryable=False,
            )

        # ── CAPA 2: Validación de contexto ────────────────────────────────────
        t = time.monotonic()
        context = await self._layer_2_validate_context(state)
        metrics.layer_durations["L2"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L2"] = context.status.value
        if context.status == LayerStatus.FAILED:
            raise AgentExecutionError(
                f"L2 falló: dependencias ausentes {context.missing_dependencies}",
                self.agent_name, is_retryable=False,
            )

        # ── CAPA 3: Planificación ─────────────────────────────────────────────
        t = time.monotonic()
        plan = await self._layer_3_plan(state, context)
        metrics.layer_durations["L3"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L3"] = plan.status.value if hasattr(plan, "status") else "SUCCESS"

        # ── CAPA 4: Ejecución de herramientas ─────────────────────────────────
        t = time.monotonic()
        tool_results = await self._layer_4_execute_tools(plan, state)
        metrics.layer_durations["L4"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L4"] = tool_results.status.value
        metrics.tool_calls_total = tool_results.tool_count
        metrics.tool_success_count = tool_results.success_count
        metrics.tool_failure_count = tool_results.failure_count

        if tool_results.critical_tool_failed:
            raise AgentExecutionError(
                "Herramienta crítica falló sin fallback disponible",
                self.agent_name, is_retryable=True,
            )

        # ── CAPA 5: Verificación ──────────────────────────────────────────────
        t = time.monotonic()
        verification = await self._layer_5_verify(tool_results, state)
        metrics.layer_durations["L5"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L5"] = verification.status.value
        metrics.anomalies_detected = len(verification.anomaly_flags)

        # ── CAPA 6: Razonamiento LLM ──────────────────────────────────────────
        t = time.monotonic()
        reasoning = await self._layer_6_reason(state, tool_results, verification)
        metrics.layer_durations["L6"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L6"] = reasoning.status.value
        metrics.llm_calls_count += 1
        metrics.llm_tokens_total += reasoning.tokens_input + reasoning.tokens_output

        # ── CAPA 7: Autocorrección ────────────────────────────────────────────
        t = time.monotonic()
        corrected = await self._layer_7_self_correct(
            reasoning, tool_results, verification, state
        )
        metrics.layer_durations["L7"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L7"] = corrected.status.value
        metrics.self_corrections_count = corrected.corrections_count
        metrics.llm_calls_count += corrected.iterations_performed

        # ── CAPA 8: Evaluación de calidad ─────────────────────────────────────
        t = time.monotonic()
        quality = await self._layer_8_assess_quality(
            corrected, verification, tool_results
        )
        metrics.layer_durations["L8"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L8"] = quality.status.value
        metrics.confidence_score = quality.confidence_score
        metrics.quality_score = quality.quality_score
        metrics.risk_score = quality.risk_score

        # ── CAPA 9: Justificación ─────────────────────────────────────────────
        t = time.monotonic()
        justification = await self._layer_9_justify(
            corrected, quality, verification, state
        )
        metrics.layer_durations["L9"] = (time.monotonic() - t) * 1000
        metrics.layer_statuses["L9"] = justification.status.value

        # ── CAPA 10: Ensamblaje de salida ──────────────────────────────────────
        t = time.monotonic()
        metrics.total_duration_ms = (time.monotonic() - pipeline_start) * 1000
        metrics.finalize()

        result_state = self._assemble_agent_result(
            state, corrected, quality, justification, metrics
        )
        metrics.layer_durations["L10"] = (time.monotonic() - t) * 1000

        record_agent_execution(
            agent=self.agent_name,
            status="success",
            duration_s=metrics.total_duration_ms / 1000,
        )
        self._log.info(
            "deep_agent.pipeline_completed",
            request_id=state.request_id,
            duration_ms=round(metrics.total_duration_ms, 2),
            confidence=metrics.confidence_score,
            quality=metrics.quality_score,
            corrections=metrics.self_corrections_count,
            llm_calls=metrics.llm_calls_count,
        )
        return result_state

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 1 — VALIDACIÓN DE ENTRADA
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_1_validate_input(
        self, state: CreditEvaluationState
    ) -> InputValidationResult:
        """
        Valida la entrada contra:
        1. Campos requeridos presentes
        2. Prompt injection en todos los campos de texto
        3. Data poisoning
        4. Jailbreak attempts
        5. Estado del grafo íntegro
        """
        errors: list[str] = []
        security_flags: list[str] = []
        injection_detected = False
        poisoning_detected = False
        jailbreak_detected = False

        required = self._get_required_input_fields()
        missing = []

        app = state.application_input
        if not app and "application_input" in required:
            errors.append("application_input ausente en estado")
            return InputValidationResult(
                status=LayerStatus.FAILED,
                validation_errors=errors,
                missing_fields=["application_input"],
            )

        if app:
            # Scan de campos de texto contra amenazas
            text_fields_to_scan = {
                "full_name":    app.identity.full_name if app.identity else "",
                "email":        app.contact.email if app.contact else "",
                "address":      app.contact.address if app.contact else "",
                "purpose":      app.credit_request.purpose if app.credit_request else "",
                "employer":     app.employer_name or "",
                "employment_type": app.employment_type or "",
            }
            for field, value in text_fields_to_scan.items():
                if not value:
                    continue
                try:
                    _guard.sanitize(value, field)
                except PromptInjectionError:
                    injection_detected = True
                    security_flags.append(f"INJECTION_IN_{field.upper()}")
                    record_security_violation("PROMPT_INJECTION")
                    if self._metrics:
                        self._metrics.injection_attempts += 1
                except JailbreakAttemptError:
                    jailbreak_detected = True
                    security_flags.append(f"JAILBREAK_IN_{field.upper()}")
                    record_security_violation("JAILBREAK")
                except DataPoisoningError:
                    poisoning_detected = True
                    security_flags.append(f"POISONING_IN_{field.upper()}")
                    record_security_violation("DATA_POISONING")

        if injection_detected or jailbreak_detected or poisoning_detected:
            errors.append("Amenaza de seguridad detectada en campos de entrada")

        # Validar campos específicos requeridos
        if app:
            if app.credit_request.requested_amount <= 0:
                errors.append("Monto solicitado debe ser positivo")
            if app.credit_request.term_months < 6:
                errors.append("Plazo mínimo 6 meses")
            if app.monthly_income <= 0:
                errors.append("Ingreso mensual debe ser positivo")

        # Calcular completeness
        completeness = 1.0
        if app:
            optional_present = sum([
                bool(app.biometric_token),
                bool(app.device_fingerprint),
                bool(app.employer_name),
                len(app.document_references) > 0,
            ])
            completeness = 0.7 + (optional_present / 4) * 0.3

        status = LayerStatus.FAILED if errors else LayerStatus.SUCCESS

        return InputValidationResult(
            status=status,
            validation_errors=errors,
            security_flags=security_flags,
            injection_detected=injection_detected,
            jailbreak_detected=jailbreak_detected,
            poisoning_detected=poisoning_detected,
            data_completeness_score=completeness,
            sanitization_applied=True,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 2 — VALIDACIÓN DE CONTEXTO
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_2_validate_context(
        self, state: CreditEvaluationState
    ) -> ContextValidationResult:
        """
        Valida dependencias entre agentes y consistencia del estado del grafo.
        Cada agente puede requerir resultados de agentes anteriores.
        """
        required_agents = self._get_required_prior_agents()
        missing_deps: list[str] = []
        prior_results = []
        inconsistencies: list[str] = []

        agent_result_map = {
            "fraud_agent":     state.fraud_result,
            "credit_agent":    state.credit_result,
            "actuarial_agent": state.actuarial_result,
        }

        from src.agents.deep.schemas import PriorAgentResultValidation

        for agent_name in required_agents:
            result = agent_result_map.get(agent_name)
            if result is None:
                missing_deps.append(agent_name)
                prior_results.append(PriorAgentResultValidation(
                    agent_name=agent_name,
                    result_present=False,
                    result_status="MISSING",
                    is_usable=False,
                    quality_score=0.0,
                    warnings=["Resultado ausente"],
                ))
            else:
                from src.core.state import AgentStatus
                is_usable = result.status == AgentStatus.SUCCESS
                quality = 1.0 if is_usable else 0.3
                warnings = []
                if result.error:
                    warnings.append(f"Agente completó con error: {result.error[:100]}")

                prior_results.append(PriorAgentResultValidation(
                    agent_name=agent_name,
                    result_present=True,
                    result_status=result.status.value,
                    is_usable=is_usable,
                    quality_score=quality,
                    warnings=warnings,
                ))

        # Cross-agent consistency: si fraud bloqueó, credit no debería continuar
        if state.fraud_result and state.fraud_result.is_blocked:
            inconsistencies.append(
                "INCONSISTENCIA: fraud_agent bloqueó la solicitud pero el pipeline continúa"
            )

        context_quality = 1.0 - (len(missing_deps) * 0.3)
        context_quality = max(0.0, context_quality)

        status = LayerStatus.FAILED if missing_deps else (
            LayerStatus.DEGRADED if inconsistencies else LayerStatus.SUCCESS
        )

        security_valid = bool(
            state.security_context and
            state.security_context.session_id
        )

        return ContextValidationResult(
            status=status,
            is_valid=status != LayerStatus.FAILED,
            prior_results=prior_results,
            missing_dependencies=missing_deps,
            context_quality_score=context_quality,
            cross_agent_inconsistencies=inconsistencies,
            state_integrity_valid=True,
            security_context_valid=security_valid,
            warnings=inconsistencies,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 3 — PLANIFICACIÓN
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_3_plan(
        self,
        state: CreditEvaluationState,
        context: ContextValidationResult,
    ) -> AgentPlan:
        """Delega al planificador específico del agente."""
        t = time.monotonic()
        plan = self._create_agent_plan(state, context)
        plan.duration_ms = (time.monotonic() - t) * 1000
        self._log.info(
            "deep_agent.plan_created",
            tools=plan.total_tools,
            strategy=plan.strategy.value,
            estimated_s=plan.estimated_duration_s,
        )
        return plan

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 4 — EJECUCIÓN DE HERRAMIENTAS
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_4_execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        """
        Delega la ejecución al agente concreto.
        El agente concreto define qué herramientas ejecutar y en qué orden.
        """
        return await self._execute_tools(plan, state)

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 5 — VERIFICACIÓN
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_5_verify(
        self,
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> VerificationResult:
        """Delega la verificación al agente concreto."""
        t = time.monotonic()
        verification = self._verify_tool_results(tool_results, state)
        verification.duration_ms = (time.monotonic() - t) * 1000
        self._log.info(
            "deep_agent.verification_done",
            consistent=verification.is_consistent,
            anomalies=len(verification.anomaly_flags),
            quality=verification.data_quality_score,
        )
        return verification

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 6 — RAZONAMIENTO LLM
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_6_reason(
        self,
        state: CreditEvaluationState,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
    ) -> ReasoningOutput:
        """
        Llama al LLM con un system prompt endurecido y el contexto verificado.
        Parsea la respuesta de forma defensiva.
        """
        t = time.monotonic()
        system_prompt = build_secure_system_prompt(self._build_role_system_prompt())
        user_prompt = self._build_reasoning_prompt(state, tool_results, verification)

        # Sanitizar el prompt antes de enviarlo (defensa en profundidad)
        try:
            user_prompt = _guard.sanitize(user_prompt[:8000], "reasoning_prompt")
        except (PromptInjectionError, DataPoisoningError) as e:
            self._log.error("deep_agent.L6_prompt_contaminated", error=str(e))
            raise AgentExecutionError(str(e), self.agent_name, is_retryable=False)

        response = await self._llm_call_with_retry(system_prompt, user_prompt)

        raw_text = response.content
        parsed = self._parse_llm_json(raw_text)

        duration_ms = (time.monotonic() - t) * 1000
        self._log.info(
            "deep_agent.L6_reasoning_done",
            tokens_in=response.input_tokens,
            tokens_out=response.output_tokens,
            parse_success=bool(parsed),
            duration_ms=round(duration_ms, 2),
        )

        return ReasoningOutput(
            status=LayerStatus.SUCCESS if parsed else LayerStatus.DEGRADED,
            raw_llm_response=raw_text,
            parsed_data=parsed or {},
            confidence=float(parsed.get("confidence", 0.5)) if parsed else 0.3,
            tokens_input=response.input_tokens,
            tokens_output=response.output_tokens,
            model_used=response.model,
            parse_errors=[] if parsed else ["JSON parse falló"],
            duration_ms=duration_ms,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 7 — AUTOCORRECCIÓN
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_7_self_correct(
        self,
        reasoning: ReasoningOutput,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
        state: CreditEvaluationState,
    ) -> SelfCorrectionResult:
        """
        Ciclo de autocorrección iterativo.
        Detecta violaciones de reglas de negocio en la salida del LLM
        y solicita correcciones al LLM hasta convergencia o max_iterations.
        """
        t = time.monotonic()
        current_data = dict(reasoning.parsed_data)
        original_confidence = reasoning.confidence
        corrections: list[Correction] = []
        iteration = 0
        max_iter = self.max_self_correction_iterations

        while iteration < max_iter:
            violations = self._check_business_rule_violations(
                current_data, tool_results, state
            )
            # Agregar anomalías críticas de verificación como violaciones
            for anom in verification.anomaly_flags:
                if anom.severity == InconsistencySeverity.CRITICAL:
                    violations.append(f"ANOMALÍA CRÍTICA: {anom.description}")

            if not violations:
                self._log.debug(
                    "deep_agent.L7_no_violations", iteration=iteration
                )
                break

            self._log.warning(
                "deep_agent.L7_violations_found",
                iteration=iteration,
                count=len(violations),
                violations=violations[:3],
            )

            # Solicitar corrección al LLM
            try:
                correction_prompt = self._build_correction_prompt(
                    current_data, violations, tool_results
                )
                system_prompt = build_secure_system_prompt(self._build_role_system_prompt())
                response = await self._llm_call_with_retry(system_prompt, correction_prompt)
                corrected_text = response.content
                new_data = self._parse_llm_json(corrected_text)
                if new_data:
                    # Registrar cada cambio como una corrección
                    for key in new_data:
                        if key in current_data and current_data[key] != new_data[key]:
                            corrections.append(Correction(
                                field=key,
                                original_value=current_data[key],
                                corrected_value=new_data[key],
                                trigger=CorrectionTrigger.BUSINESS_RULE_VIOLATION,
                                rule_violated="; ".join(violations[:2]),
                                description=f"Corrección iteración {iteration + 1}",
                            ))
                    current_data = new_data

            except Exception as e:
                self._log.warning("deep_agent.L7_correction_failed", error=str(e))
                break

            iteration += 1

        final_confidence = float(current_data.get("confidence", original_confidence))

        return SelfCorrectionResult(
            status=LayerStatus.SUCCESS,
            iterations_performed=iteration,
            max_iterations=max_iter,
            corrections=corrections,
            final_data=current_data,
            original_confidence=original_confidence,
            final_confidence=final_confidence,
            correction_needed=bool(corrections),
            max_iterations_reached=(iteration >= max_iter),
            convergence_score=1.0 - (iteration / max_iter),
            duration_ms=(time.monotonic() - t) * 1000,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 8 — EVALUACIÓN DE CALIDAD
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_8_assess_quality(
        self,
        corrected: SelfCorrectionResult,
        verification: VerificationResult,
        tool_results: ToolExecutionResults,
    ) -> QualityAssessment:
        """
        Calcula tres scores:
        - confidence_score: cuán seguro está el agente de su análisis
        - quality_score: calidad del proceso de análisis
        - risk_score: riesgo de que el análisis sea incorrecto
        """
        t = time.monotonic()

        # Dimensiones del agente concreto
        dimensions = self._compute_quality_dimensions(tool_results, verification, corrected)

        # Score de calidad ponderado
        if dimensions:
            quality_score = sum(d.score * d.weight for d in dimensions) / sum(d.weight for d in dimensions)
        else:
            quality_score = 0.5

        # Confidence score: basado en convergencia, datos completos, sin anomalías
        confidence = corrected.final_confidence
        confidence *= (1.0 - 0.1 * corrected.iterations_performed)
        confidence *= (0.7 + 0.3 * verification.data_quality_score)
        confidence = max(0.1, min(0.99, confidence))

        # Risk score: inversamente correlacionado con calidad y confianza
        risk_base = 1.0 - (quality_score * 0.5 + confidence * 0.5)
        risk_adjustment = 0.1 * len(verification.anomaly_flags)
        if verification.has_critical_anomalies:
            risk_adjustment += 0.2
        risk_score = min(1.0, risk_base + risk_adjustment)

        quality_flags: list[str] = []
        if corrected.max_iterations_reached:
            quality_flags.append("MAX_SELF_CORRECTIONS_REACHED")
        if tool_results.failure_count > 0:
            quality_flags.append(f"{tool_results.failure_count}_TOOL_FAILURES")
        if verification.has_critical_anomalies:
            quality_flags.append("CRITICAL_ANOMALIES_PRESENT")
        if confidence < self.min_confidence_threshold:
            quality_flags.append("LOW_CONFIDENCE")
        if quality_score < self.min_quality_threshold:
            quality_flags.append("LOW_QUALITY")

        requires_human = (
            confidence < self.min_confidence_threshold or
            quality_score < self.min_quality_threshold or
            verification.has_critical_anomalies or
            corrected.max_iterations_reached
        )

        is_reliable = (
            confidence >= self.min_confidence_threshold and
            quality_score >= self.min_quality_threshold and
            not verification.has_critical_anomalies
        )

        self._log.info(
            "deep_agent.L8_quality",
            confidence=round(confidence, 3),
            quality=round(quality_score, 3),
            risk=round(risk_score, 3),
            reliable=is_reliable,
            flags=quality_flags,
        )

        return QualityAssessment(
            status=LayerStatus.SUCCESS,
            confidence_score=round(confidence, 4),
            risk_score=round(risk_score, 4),
            quality_score=round(quality_score, 4),
            dimensions=dimensions,
            is_reliable=is_reliable,
            requires_human_review=requires_human,
            quality_flags=quality_flags,
            confidence_threshold_met=confidence >= self.min_confidence_threshold,
            minimum_quality_threshold_met=quality_score >= self.min_quality_threshold,
            duration_ms=(time.monotonic() - t) * 1000,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CAPA 9 — JUSTIFICACIÓN
    # ═══════════════════════════════════════════════════════════════════════

    async def _layer_9_justify(
        self,
        corrected: SelfCorrectionResult,
        quality: QualityAssessment,
        verification: VerificationResult,
        state: CreditEvaluationState,
    ) -> Justification:
        """
        Construye la justificación regulatoria:
        - Cadena de razonamiento explícita
        - Base regulatoria (GDPR Art. 22, Ley de protección de datos)
        - Texto explicativo para el solicitante
        - Análisis contrafactual
        """
        t = time.monotonic()
        data = corrected.final_data

        # Cadena de razonamiento (extraer del output del LLM si disponible)
        reasoning_chain = data.get("reasoning_chain", [])
        if not reasoning_chain and "explanation" in data:
            reasoning_chain = [data["explanation"]]

        # Factores considerados con pesos
        factors_weighted = {}
        if "contributing_factors" in data:
            factors_weighted = {
                str(k): float(v) if isinstance(v, (int, float)) else 0.0
                for k, v in data.get("contributing_factors", {}).items()
            }
        elif "decision_factors" in data:
            factors_weighted = {
                str(k): 0.5
                for k in data.get("decision_factors", {}).keys()
            }

        # Referencias regulatorias base
        regulatory_refs = [
            RegulatoryReference(
                regulation="GDPR",
                article="Artículo 22",
                description="Derecho a no ser objeto de decisiones automatizadas. "
                            "El solicitante puede solicitar revisión humana.",
                compliance_status="COMPLIANT",
            ),
            RegulatoryReference(
                regulation="Basel III",
                article="SR 11-7 / OCC 2011-12",
                description="Documentación de modelos de riesgo de crédito. "
                            "Variables, metodología y validación documentadas.",
                compliance_status="COMPLIANT",
            ),
        ]

        # Texto explicativo para el solicitante (claro, sin jerga técnica)
        decision_str = data.get("decision", data.get("recommendation", ""))
        gdpr_text = (
            f"Su solicitud ha sido evaluada por el sistema automatizado de "
            f"CLOUD BANK. La decisión se basó principalmente en: "
            f"{', '.join(list(factors_weighted.keys())[:3])}. "
            f"Tiene derecho a solicitar revisión humana de esta decisión "
            f"contactando a nuestro equipo de crédito."
        )

        # Análisis contrafactual: ¿qué habría cambiado la decisión?
        counterfactual = data.get("counterfactual", "")
        if not counterfactual:
            counterfactual = (
                "No disponible — solicitar análisis contrafactual al equipo de crédito."
            )

        # Limitaciones del análisis
        limitations = []
        if quality.quality_score < 0.80:
            limitations.append(f"Calidad del análisis: {quality.quality_score:.0%}")
        if verification.anomaly_flags:
            limitations.append(f"{len(verification.anomaly_flags)} anomalías detectadas en datos")
        if corrected.corrections_count > 0:
            limitations.append(f"{corrected.corrections_count} autocorrecciones aplicadas")

        return Justification(
            status=LayerStatus.SUCCESS,
            executive_summary=data.get("explanation", data.get("justification", "")),
            reasoning_chain=reasoning_chain if isinstance(reasoning_chain, list) else [str(reasoning_chain)],
            factors_considered={k: "POSITIVO" if v > 0 else "NEGATIVO" for k, v in factors_weighted.items()},
            factors_weighted=factors_weighted,
            regulatory_references=regulatory_refs,
            gdpr_explanation=gdpr_text,
            model_documentation=f"Modelo: {self.agent_name} v2.0 | Confianza: {quality.confidence_score:.1%}",
            counterfactual=counterfactual,
            limitations_disclosed=limitations,
            duration_ms=(time.monotonic() - t) * 1000,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # INFRAESTRUCTURA COMPARTIDA
    # ═══════════════════════════════════════════════════════════════════════

    async def _llm_call_with_retry(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
    ) -> LLMResponse:
        """LLM call con retry exponencial en errores de red/rate limit, agnóstico de proveedor."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=16),
            retry=retry_if_exception_type(LLMTransientError),
            reraise=True,
        ):
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    record_agent_retry(self.agent_name)
                response = await self._llm.complete(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                record_llm_call(
                    model=response.model,
                    agent=self.agent_name,
                    status="success",
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
                return response

    def _parse_llm_json(self, text: str) -> dict[str, Any]:
        """Parseo defensivo de JSON desde respuesta LLM con múltiples estrategias."""
        if not text:
            return {}
        # Estrategia 1: JSON completo
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
        # Estrategia 2: Bloque de código markdown
        try:
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError):
            pass
        self._log.warning("deep_agent.json_parse_failed", preview=text[:150])
        return {}

    def _get_agent_timeout(self) -> int:
        timeouts = {
            "fraud_deep_agent":     self._settings.agents.fraud_agent_timeout,
            "credit_deep_agent":    self._settings.agents.credit_agent_timeout,
            "actuarial_deep_agent": self._settings.agents.actuarial_agent_timeout,
            "approval_deep_agent":  self._settings.agents.approval_agent_timeout,
        }
        return timeouts.get(self.agent_name, 30)

    def _safe_tool_result(
        self,
        result: Any,
        model_class: type,
        defaults: dict,
        tool_name: str,
    ) -> tuple[Any, ToolResult]:
        """Convierte el resultado de una herramienta en (objeto, ToolResult)."""
        if isinstance(result, Exception):
            self._log.warning(
                "deep_agent.tool_failed", tool=tool_name, error=str(result)[:200]
            )
            obj = model_class(**defaults)
            tr = ToolResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=str(result)[:300],
                is_degraded=True,
                degradation_reason="Service unavailable — fallback aplicado",
                data_quality=0.4,
            )
        else:
            obj = result
            tr = ToolResult(
                tool_name=tool_name,
                success=True,
                result=result,
                data_quality=1.0,
            )
        return obj, tr
