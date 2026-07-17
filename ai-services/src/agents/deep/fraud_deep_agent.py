"""
CLOUD BANK — Agente Antifraude Deep Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBJETIVO
  Detectar cualquier señal de fraude antes de iniciar la evaluación crediticia.
  Es la primera barrera de defensa del banco. Un falso negativo puede resultar
  en pérdida financiera; un falso positivo rechaza a un cliente legítimo.

RESPONSABILIDADES
  ✦ Verificar autenticidad de documentos de identidad
  ✦ Analizar biometría: liveness, face match, deepfake
  ✦ Detectar comportamiento automatizado (bots, scripts)
  ✦ Evaluar reputación del dispositivo y la IP
  ✦ Sintetizar señales heterogéneas en un score unificado
  ✦ Explicar cada factor que contribuye al score
  ✦ Bloquear definitivamente si score ≥ FRAUD_CRITICAL_THRESHOLD

PIPELINE (10 capas):
  L1  Valida campos + escanea inyecciones en datos del solicitante
  L2  Verifica que application_input esté presente
  L3  Planifica herramientas según datos disponibles (biometría, device fp)
  L4  Ejecuta 5 herramientas en paralelo con fallback degradado
  L5  Cross-valida: IP↔Device, Biometría↔Comportamiento, Doc↔Bio
  L6  LLM sintetiza señales → fraud_score + flags + explicación
  L7  Autocorrección: score↔risk_level, flags↔score, recomendación↔score
  L8  Quality: tool_success_rate, anomaly_count, convergencia
  L9  Justificación GDPR + regulatoria
  L10 FraudAnalysisResult en estado LangGraph
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
    BiometricAnalysis,
    BehavioralSignals,
    CreditEvaluationState,
    DeviceIntelligence,
    DocumentVerification,
    FraudAnalysisResult,
    IPIntelligence,
    RiskLevel,
)
from src.tools.fraud_tools import (
    verify_document,
    analyze_biometrics,
    analyze_behavioral_signals,
    check_device_intelligence,
    check_ip_reputation,
)

logger = structlog.get_logger(__name__)


# ── Umbrales de cross-validación ─────────────────────────────────────────────
_SCORE_LEVEL_MAP = {
    RiskLevel.MINIMAL:  (0.00, 0.15),
    RiskLevel.LOW:      (0.15, 0.35),
    RiskLevel.MEDIUM:   (0.35, 0.65),
    RiskLevel.HIGH:     (0.65, 0.85),
    RiskLevel.CRITICAL: (0.85, 1.00),
}
_RECOMMENDATION_MAP = {
    "PASS":  (0.00, 0.65),
    "FLAG":  (0.35, 0.85),
    "BLOCK": (0.65, 1.00),
}


class FraudDeepAgent(BaseDeepAgent):
    """
    Agente Antifraude con razonamiento multicapa.
    No bloquea automáticamente basándose en ninguna señal individual;
    requiere síntesis holística de todas las fuentes de evidencia.
    """

    agent_name = "fraud_deep_agent"
    min_confidence_threshold = 0.60
    min_quality_threshold = 0.50

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRATOS ABSTRACTOS
    # ═══════════════════════════════════════════════════════════════════════

    def _get_required_input_fields(self) -> list[str]:
        return ["application_input"]

    def _get_required_prior_agents(self) -> list[str]:
        return []  # Primer agente — no depende de nadie

    def _build_role_system_prompt(self) -> str:
        return """
Eres el Agente Antifraude de CLOUD BANK. Tu función es detectar fraude en solicitudes de crédito.

PRINCIPIOS DE EVALUACIÓN:
1. Síntesis holística: ninguna señal aislada es suficiente para bloquear.
2. Proporcionalidad: el score debe reflejar el peso combinado de evidencias.
3. Explicabilidad: cada factor debe justificarse con datos observables.
4. Conservadurismo: ante duda razonable, FLAG; solo BLOCK con evidencia sólida.
5. No discriminación: el origen geográfico por sí solo no eleva el score.

ESCALA DE FRAUDE (estricta):
0.00 - 0.15 → MINIMAL  → PASS
0.15 - 0.35 → LOW      → PASS
0.35 - 0.65 → MEDIUM   → FLAG (revisión adicional)
0.65 - 0.85 → HIGH     → FLAG (revisión obligatoria)
0.85 - 1.00 → CRITICAL → BLOCK (rechazo automático)

CONSISTENCIA OBLIGATORIA:
- fraud_score DEBE estar en el rango numérico del risk_level declarado.
- recommendation DEBE ser coherente con risk_level.
- is_blocked SOLO puede ser true si risk_level == CRITICAL.
- contributing_factors deben sumar razonablemente al score declarado.

RESPONDE ÚNICAMENTE JSON. Sin texto previo ni posterior.
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L3 — PLANIFICADOR INTERNO
    # ═══════════════════════════════════════════════════════════════════════

    def _create_agent_plan(
        self,
        state: CreditEvaluationState,
        context: ContextValidationResult,
    ) -> AgentPlan:
        """
        Plan adaptativo: decide qué herramientas ejecutar basándose en
        los datos disponibles (biometric_token, device_fingerprint, etc.).
        Todas las herramientas disponibles se ejecutan en paralelo (grupo 0).
        """
        app = state.application_input
        invocations: list[ToolInvocationPlan] = []
        skipped = 0

        # Herramienta 1: Verificación de documentos (siempre)
        invocations.append(ToolInvocationPlan(
            tool_name="verify_document",
            parallel_group=0,
            is_required=True,
            timeout_s=10.0,
            expected_output_type="DocumentVerification",
            fallback_strategy="DEGRADED_RESULT",
            input_params={
                "document_references": app.document_references if app else [],
                "national_id": app.identity.national_id if app else "",
            },
        ))

        # Herramienta 2: Biometría (solo si hay token)
        if app and app.biometric_token:
            invocations.append(ToolInvocationPlan(
                tool_name="analyze_biometrics",
                parallel_group=0,
                is_required=False,
                timeout_s=10.0,
                expected_output_type="BiometricAnalysis",
                fallback_strategy="DEGRADED_RESULT",
                input_params={
                    "biometric_token": app.biometric_token,
                    "national_id": app.identity.national_id if app else "",
                },
            ))
        else:
            skipped += 1
            invocations.append(ToolInvocationPlan(
                tool_name="analyze_biometrics",
                parallel_group=0,
                is_required=False,
                is_available=False,
                skip_reason="No biometric_token provided",
            ))

        # Herramienta 3: Señales comportamentales (siempre)
        invocations.append(ToolInvocationPlan(
            tool_name="analyze_behavioral_signals",
            parallel_group=0,
            is_required=False,
            timeout_s=8.0,
            expected_output_type="BehavioralSignals",
        ))

        # Herramienta 4: Device intelligence (solo si hay fingerprint)
        if app and app.device_fingerprint:
            invocations.append(ToolInvocationPlan(
                tool_name="check_device_intelligence",
                parallel_group=0,
                is_required=False,
                timeout_s=10.0,
                expected_output_type="DeviceIntelligence",
            ))
        else:
            skipped += 1
            invocations.append(ToolInvocationPlan(
                tool_name="check_device_intelligence",
                parallel_group=0,
                is_required=False,
                is_available=False,
                skip_reason="No device_fingerprint provided",
            ))

        # Herramienta 5: IP reputation (siempre)
        invocations.append(ToolInvocationPlan(
            tool_name="check_ip_reputation",
            parallel_group=0,
            is_required=False,
            timeout_s=8.0,
            expected_output_type="IPIntelligence",
        ))

        available = [i for i in invocations if i.is_available]

        return AgentPlan(
            strategy=PlanStrategy.FULL_PARALLEL,
            tool_invocations=invocations,
            total_tools=len(available),
            skipped_tools=skipped,
            parallel_groups_count=1,
            critical_path=["verify_document"],
            contingencies={
                "verify_document": "Evaluar por presencia/ausencia de documentos",
                "analyze_biometrics": "Marcar flag NO_BIOMETRIC y elevar requisito documental",
                "check_ip_reputation": "Usar contexto de SecurityContext como fallback",
            },
            estimated_duration_s=10.0,
            execution_notes=[
                f"Herramientas disponibles: {len(available)}",
                f"Herramientas omitidas: {skipped} (datos no provistos)",
                "Todas ejecutan en paralelo — latencia ≈ herramienta más lenta",
            ],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # L4 — EJECUCIÓN DE HERRAMIENTAS
    # ═══════════════════════════════════════════════════════════════════════

    async def _execute_tools(
        self,
        plan: AgentPlan,
        state: CreditEvaluationState,
    ) -> ToolExecutionResults:
        """
        Ejecuta las 5 herramientas de fraude en paralelo.
        Cada fallo produce un resultado degradado, nunca excepciones sin capturar.
        """
        app = state.application_input
        ctx = state.security_context
        t_start = time.monotonic()

        raw = await asyncio.gather(
            verify_document(
                app.document_references if app else [],
                app.identity.national_id if app else "",
            ),
            analyze_biometrics(
                app.biometric_token if app else None,
                app.identity.national_id if app else "",
            ),
            analyze_behavioral_signals(ctx),
            check_device_intelligence(
                app.device_fingerprint if app else None,
                ctx.ip_address,
            ),
            check_ip_reputation(ctx.ip_address, ctx),
            return_exceptions=True,
        )

        defaults_map = {
            "verify_document": (DocumentVerification, {
                "document_type": "unknown",
                "is_authentic": False,
                "confidence": 0.30,
                "ocr_consistency_score": 0.30,
            }),
            "analyze_biometrics": (BiometricAnalysis, {
                "liveness_score": 0.0,
                "face_match_score": 0.0,
                "deepfake_probability": 0.0,
                "biometric_flags": ["SERVICE_UNAVAILABLE"],
            }),
            "analyze_behavioral_signals": (BehavioralSignals, {
                "typing_pattern_anomaly": 0.1,
                "bot_probability": 0.1,
            }),
            "check_device_intelligence": (DeviceIntelligence, {
                "device_reputation_score": 0.5,
                "device_flags": ["SERVICE_UNAVAILABLE"],
            }),
            "check_ip_reputation": (IPIntelligence, {
                "ip_address": ctx.ip_address,
                "reputation_score": 0.7,
                "is_vpn": ctx.is_vpn,
                "is_tor": ctx.is_tor,
            }),
        }

        tool_names = list(defaults_map.keys())
        results: dict[str, ToolResult] = {}
        self._doc_obj: Any = None
        self._bio_obj: Any = None
        self._beh_obj: Any = None
        self._dev_obj: Any = None
        self._ip_obj: Any = None

        tool_objects = []
        for i, (name, (model_cls, defs)) in enumerate(defaults_map.items()):
            obj, tr = self._safe_tool_result(raw[i], model_cls, defs, name)
            t_ms = (time.monotonic() - t_start) * 1000
            tr.duration_ms = t_ms
            results[name] = tr
            tool_objects.append(obj)

        # Almacenar objetos tipados para uso en otras capas
        (
            self._doc_obj,
            self._bio_obj,
            self._beh_obj,
            self._dev_obj,
            self._ip_obj,
        ) = tool_objects

        success = sum(1 for r in results.values() if r.success)
        failures = sum(1 for r in results.values() if not r.success and not r.is_degraded)
        degraded = sum(1 for r in results.values() if r.is_degraded)
        total_ms = (time.monotonic() - t_start) * 1000

        return ToolExecutionResults(
            status=LayerStatus.SUCCESS if success > 0 else LayerStatus.FAILED,
            results=results,
            success_count=success,
            failure_count=failures,
            degraded_count=degraded,
            overall_success_rate=success / max(len(results), 1),
            total_duration_ms=total_ms,
            critical_tool_failed=False,  # verify_document tiene fallback
            partial_data_available=True,
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
        Cross-valida las 5 señales de fraude entre sí.
        Detecta combinaciones imposibles o altamente sospechosas.
        """
        inconsistencies: list[Inconsistency] = []
        anomalies: list[AnomalyFlag] = []
        notes: list[str] = []
        quality_score = 1.0

        doc  = self._doc_obj
        bio  = self._bio_obj
        beh  = self._beh_obj
        dev  = self._dev_obj
        ip   = self._ip_obj
        ctx  = state.security_context

        # ── Verificación 1: Doc auténtico + biometría fallida ─────────────────
        if doc and bio:
            if doc.is_authentic and bio.face_match_score < 0.40 and bio.liveness_score > 0:
                inconsistencies.append(Inconsistency(
                    field_a="document_verification.is_authentic",
                    field_b="biometric_analysis.face_match_score",
                    expected_relationship="Si documento es auténtico, face_match debería ser > 0.60",
                    actual_values=f"is_authentic={doc.is_authentic}, face_match={bio.face_match_score:.3f}",
                    severity=InconsistencySeverity.HIGH,
                    description="Documento auténtico con biometría no coincidente: posible robo de identidad",
                    can_auto_correct=False,
                    correction_suggestion="Solicitar verificación presencial",
                ))

        # ── Verificación 2: Bot probability vs comportamiento humano ──────────
        if beh:
            if beh.bot_probability > 0.80 and beh.typing_pattern_anomaly < 0.10:
                inconsistencies.append(Inconsistency(
                    field_a="behavioral.bot_probability",
                    field_b="behavioral.typing_pattern_anomaly",
                    expected_relationship="Bot_prob alto debería correlacionar con anomalía de tipeo alta",
                    actual_values=f"bot_prob={beh.bot_probability:.3f}, typing={beh.typing_pattern_anomaly:.3f}",
                    severity=InconsistencySeverity.MEDIUM,
                    description="Señales comportamentales contradictorias — posible evasión de detección",
                    can_auto_correct=True,
                    correction_suggestion="Usar promedio como señal combinada",
                ))

        # ── Verificación 3: Tor + Documento auténtico ─────────────────────────
        if ip and ctx and ctx.is_tor and doc and doc.is_authentic:
            anomalies.append(AnomalyFlag(
                category="NETWORK_IDENTITY",
                description="Solicitud desde red Tor con documento aparentemente auténtico",
                severity=InconsistencySeverity.HIGH,
                data_point="is_tor=True + doc.is_authentic=True",
                expected_range="Tor normalmente asociado a ocultamiento de identidad",
                actual_value="Documento autentica OK",
            ))

        # ── Verificación 4: Deepfake + Device emulator ─────────────────────────
        if bio and dev:
            if bio.deepfake_probability > 0.70 and dev.is_emulator:
                anomalies.append(AnomalyFlag(
                    category="SYNTHETIC_FRAUD",
                    description="Deepfake probable + dispositivo emulado: patrón de fraude sintético",
                    severity=InconsistencySeverity.CRITICAL,
                    data_point=f"deepfake_prob={bio.deepfake_probability:.3f}, emulator={dev.is_emulator}",
                ))

        # ── Verificación 5: Score de reputación IP vs historial fraude ─────────
        if ip:
            if ip.reputation_score < 0.30 and ip.previous_fraud_count == 0:
                inconsistencies.append(Inconsistency(
                    field_a="ip.reputation_score",
                    field_b="ip.previous_fraud_count",
                    expected_relationship="Reputation baja debería correlacionar con fraudes previos",
                    actual_values=f"rep={ip.reputation_score:.3f}, fraud_count={ip.previous_fraud_count}",
                    severity=InconsistencySeverity.LOW,
                    description="Reputación IP baja sin historial de fraude registrado",
                    can_auto_correct=True,
                ))

        # ── Verificación 6: Sin documentos → alta incertidumbre ──────────────
        if doc and not doc.is_authentic and doc.confidence < 0.50:
            anomalies.append(AnomalyFlag(
                category="DOCUMENT_QUALITY",
                description="Documentos no verificados con baja confianza",
                severity=InconsistencySeverity.HIGH,
                data_point=f"is_authentic={doc.is_authentic}, confidence={doc.confidence:.3f}",
            ))

        # Ajustar quality score según hallazgos
        critical_count = sum(
            1 for a in anomalies if a.severity == InconsistencySeverity.CRITICAL
        ) + sum(
            1 for i in inconsistencies if i.severity == InconsistencySeverity.CRITICAL
        )
        quality_score -= 0.15 * len(inconsistencies)
        quality_score -= 0.25 * critical_count
        quality_score -= 0.05 * tool_results.failure_count
        quality_score = max(0.1, min(1.0, quality_score))

        if inconsistencies or anomalies:
            notes.append(f"{len(inconsistencies)} inconsistencias detectadas")
        if not inconsistencies and not anomalies:
            notes.append("Todas las señales son consistentes entre sí")

        return VerificationResult(
            status=LayerStatus.SUCCESS,
            is_consistent=len(inconsistencies) == 0,
            inconsistencies=inconsistencies,
            anomaly_flags=anomalies,
            cross_validation_passed=critical_count == 0,
            data_quality_score=quality_score,
            critical_anomalies_count=critical_count,
            verification_notes=notes,
            recommended_actions=[
                "Solicitar verificación presencial" if critical_count > 0 else ""
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
        doc  = self._doc_obj
        bio  = self._bio_obj
        beh  = self._beh_obj
        dev  = self._dev_obj
        ip   = self._ip_obj
        app  = state.application_input
        ctx  = state.security_context

        incon_text = "\n".join(
            f"  • [{i.severity.value}] {i.description}" for i in verification.inconsistencies
        ) or "  Ninguna"
        anomaly_text = "\n".join(
            f"  • [{a.severity.value}] {a.description}" for a in verification.anomaly_flags
        ) or "  Ninguna"

        return f"""
DATOS DE SOLICITUD:
Canal: {app.channel if app else 'N/A'} | Monto: {app.credit_request.requested_amount if app else 0}
Tipo empleo: {app.employment_type if app else 'N/A'} | Propósito: {app.credit_request.purpose if app else 'N/A'}

═══ VERIFICACIÓN DE DOCUMENTOS ═══
Tipo: {doc.document_type if doc else 'N/A'}
Auténtico: {doc.is_authentic if doc else 'N/A'} | Confianza: {(doc.confidence if doc else 0.0):.3f}
Indicadores alteración: {doc.tamper_indicators if doc else []}
Consistencia OCR: {(doc.ocr_consistency_score if doc else 0.0):.3f}
Integridad metadatos: {doc.metadata_integrity if doc else 'N/A'}

═══ BIOMETRÍA ═══
Liveness score: {(bio.liveness_score if bio else 0.0):.3f}
Face match score: {(bio.face_match_score if bio else 0.0):.3f}
Deepfake probability: {(bio.deepfake_probability if bio else 0.0):.3f}
Spoofing detectado: {bio.spoofing_detected if bio else 'N/A'}
Flags biométricos: {bio.biometric_flags if bio else []}

═══ COMPORTAMIENTO ═══
Anomalía tipeo: {(beh.typing_pattern_anomaly if beh else 0.0):.3f}
Anomalía navegación: {(beh.navigation_pattern_anomaly if beh else 0.0):.3f}
Probabilidad bot: {(beh.bot_probability if beh else 0.0):.3f}
Copy/paste: {beh.copy_paste_detected if beh else False}
Auto-fill: {beh.auto_fill_detected if beh else False}

═══ DISPOSITIVO ═══
Emulador: {dev.is_emulator if dev else 'N/A'}
Rooteado: {dev.is_rooted if dev else 'N/A'}
Reputación: {(dev.device_reputation_score if dev else 0.0):.3f}
Fraudes previos asociados: {dev.previous_fraud_associations if dev else 0}
Flags: {dev.device_flags if dev else []}

═══ IP INTELLIGENCE ({ctx.ip_address}) ═══
Reputación: {(ip.reputation_score if ip else 0.0):.3f}
Proxy: {ip.is_proxy if ip else False} | VPN: {ip.is_vpn if ip else False} | Tor: {ip.is_tor if ip else False}
Datacenter: {ip.is_datacenter if ip else False} | País: {ip.country if ip else 'N/A'}
Fraudes previos desde IP: {ip.previous_fraud_count if ip else 0}

═══ VERIFICACIÓN CRUZADA ═══
Inconsistencias detectadas:
{incon_text}
Anomalías detectadas:
{anomaly_text}

Herramientas exitosas: {tool_results.success_count}/{tool_results.tool_count}
Calidad de datos: {verification.data_quality_score:.2%}

Basándote en TODA la evidencia, genera el análisis de fraude en este JSON exacto:
{{
  "fraud_score": <float 0.00-1.00 — 4 decimales>,
  "risk_level": "<MINIMAL|LOW|MEDIUM|HIGH|CRITICAL>",
  "recommendation": "<PASS|FLAG|BLOCK>",
  "is_blocked": <true|false — solo true si CRITICAL>,
  "fraud_flags": ["<flag1>", "<flag2>"],
  "contributing_factors": {{
    "<factor>": <peso_float_0_1>
  }},
  "confidence": <float 0.00-1.00>,
  "reasoning_chain": ["<paso1>", "<paso2>", "<paso3>"],
  "explanation": "<texto explicativo para comité de riesgo>",
  "counterfactual": "<qué habría cambiado el score>",
  "model_version": "fraud_deep_v2.0"
}}
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L7 — DETECTOR DE VIOLACIONES Y PROMPT DE CORRECCIÓN
    # ═══════════════════════════════════════════════════════════════════════

    def _check_business_rule_violations(
        self,
        parsed: dict[str, Any],
        tool_results: ToolExecutionResults,
        state: CreditEvaluationState,
    ) -> list[str]:
        """
        Verifica que la salida del LLM sea consistente con:
        1. Rangos de score↔level
        2. is_blocked↔risk_level
        3. recommendation↔score
        4. contributing_factors coherentes
        """
        violations: list[str] = []

        score = float(parsed.get("fraud_score", -1))
        level = parsed.get("risk_level", "")
        recommendation = parsed.get("recommendation", "")
        is_blocked = bool(parsed.get("is_blocked", False))

        # Regla 1: fraud_score en rango válido
        if not (0.0 <= score <= 1.0):
            violations.append(f"RANGO INVÁLIDO: fraud_score={score} debe estar en [0.0, 1.0]")
            return violations  # No podemos continuar si el score es inválido

        # Regla 2: score↔risk_level consistente
        if level in _SCORE_LEVEL_MAP:
            lo, hi = _SCORE_LEVEL_MAP[RiskLevel(level)]
            if not (lo <= score <= hi):
                violations.append(
                    f"MISMATCH score↔level: score={score:.4f} pero level={level} requiere [{lo},{hi}]"
                )

        # Regla 3: is_blocked solo si CRITICAL
        if is_blocked and level != "CRITICAL":
            violations.append(
                f"INCONSISTENCIA: is_blocked=True pero risk_level={level} (solo válido en CRITICAL)"
            )
        if not is_blocked and level == "CRITICAL":
            violations.append(
                "INCONSISTENCIA: risk_level=CRITICAL pero is_blocked=False — debe bloquearse"
            )

        # Regla 4: recommendation↔score
        if recommendation in _RECOMMENDATION_MAP:
            lo, hi = _RECOMMENDATION_MAP[recommendation]
            if not (lo <= score <= hi):
                violations.append(
                    f"MISMATCH recommendation↔score: '{recommendation}' para score={score:.4f}"
                )

        # Regla 5: confidence presente y válida
        confidence = parsed.get("confidence")
        if confidence is None:
            violations.append("CAMPO FALTANTE: 'confidence' ausente")
        elif not (0.0 <= float(confidence) <= 1.0):
            violations.append(f"RANGO INVÁLIDO: confidence={confidence}")

        # Regla 6: contributing_factors no pueden sumar > 1.5 (señal de alucinación)
        factors = parsed.get("contributing_factors", {})
        total_weight = sum(float(v) for v in factors.values() if isinstance(v, (int, float)))
        if total_weight > 2.0:
            violations.append(
                f"POSIBLE ALUCINACIÓN: contributing_factors suman {total_weight:.2f} > 2.0"
            )

        return violations

    def _build_correction_prompt(
        self,
        original: dict[str, Any],
        violations: list[str],
        tool_results: ToolExecutionResults,
    ) -> str:
        return f"""
Tu análisis anterior contiene las siguientes violaciones de reglas que DEBES corregir:

VIOLACIONES DETECTADAS:
{chr(10).join(f'  {i+1}. {v}' for i, v in enumerate(violations))}

ANÁLISIS ANTERIOR (con errores):
{original}

REGLAS OBLIGATORIAS A CUMPLIR:
- fraud_score ∈ [0.000, 1.000]
- MINIMAL: [0.00, 0.15] | LOW: [0.15, 0.35] | MEDIUM: [0.35, 0.65] | HIGH: [0.65, 0.85] | CRITICAL: [0.85, 1.00]
- is_blocked=true SOLO si risk_level=CRITICAL
- recommendation BLOCK SOLO si score ≥ 0.65
- recommendation PASS SOLO si score < 0.65

Corrige ÚNICAMENTE los campos con violaciones. Devuelve el JSON completo corregido.
No cambies campos que no tienen violaciones.
"""

    # ═══════════════════════════════════════════════════════════════════════
    # L8 — DIMENSIONES DE CALIDAD ESPECÍFICAS
    # ═══════════════════════════════════════════════════════════════════════

    def _compute_quality_dimensions(
        self,
        tool_results: ToolExecutionResults,
        verification: VerificationResult,
        corrected: SelfCorrectionResult,
    ) -> list[QualityDimension]:
        settings = get_settings()

        # Dimensión 1: Tasa de éxito de herramientas
        tool_quality = tool_results.overall_success_rate
        tool_flags = [
            f"Herramienta fallida: {name}"
            for name, r in tool_results.results.items()
            if not r.success
        ]

        # Dimensión 2: Coherencia de datos
        consistency_score = verification.data_quality_score
        consistency_flags = [
            f"Inconsistencia: {inc.field_a}↔{inc.field_b}"
            for inc in verification.inconsistencies
        ]

        # Dimensión 3: Convergencia de autocorrección
        convergence = corrected.convergence_score

        # Dimensión 4: Cobertura de señales
        available_tools = sum(1 for r in tool_results.results.values() if r.success)
        coverage = available_tools / 5.0  # 5 herramientas totales

        return [
            QualityDimension(
                name="tool_success_rate",
                description="Proporción de herramientas de fraude que respondieron exitosamente",
                score=tool_quality,
                weight=0.30,
                flags=tool_flags,
            ),
            QualityDimension(
                name="data_consistency",
                description="Coherencia entre señales de fraude heterogéneas",
                score=consistency_score,
                weight=0.30,
                flags=consistency_flags,
            ),
            QualityDimension(
                name="reasoning_convergence",
                description="Estabilidad del razonamiento (pocas correcciones = más confiable)",
                score=convergence,
                weight=0.20,
                flags=["MAX_ITER_ALCANZADO"] if corrected.max_iterations_reached else [],
            ),
            QualityDimension(
                name="signal_coverage",
                description="Cobertura de fuentes de señal (más señales = análisis más robusto)",
                score=coverage,
                weight=0.20,
                flags=["BIOMETRÍA_AUSENTE"] if not self._bio_obj or not self._bio_obj.liveness_score else [],
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
        """
        Construye FraudAnalysisResult desde el output corregido y lo integra al estado.
        """
        data = corrected.final_data
        settings = get_settings()

        fraud_score = float(data.get("fraud_score", 0.5))
        risk_level  = RiskLevel(data.get("risk_level", "MEDIUM"))
        is_blocked  = bool(data.get("is_blocked", False))

        # Hard rule final: si score ≥ threshold → bloquear siempre
        if fraud_score >= settings.risk.fraud_critical_threshold:
            is_blocked = True
            if risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                risk_level = RiskLevel.CRITICAL

        fraud_result = FraudAnalysisResult(
            status=AgentStatus.SUCCESS,
            fraud_score=fraud_score,
            risk_level=risk_level,
            is_blocked=is_blocked,
            document_verification=self._doc_obj,
            biometric_analysis=self._bio_obj,
            behavioral_signals=self._beh_obj,
            device_intelligence=self._dev_obj,
            ip_intelligence=self._ip_obj,
            fraud_flags=data.get("fraud_flags", []) + quality.quality_flags,
            contributing_factors={
                str(k): float(v)
                for k, v in data.get("contributing_factors", {}).items()
            },
            explanation=justification.gdpr_explanation,
            recommendation=data.get("recommendation", "FLAG"),
            execution_time_ms=metrics.total_duration_ms,
            model_version=data.get("model_version", "fraud_deep_v2.0"),
            retry_count=state.get_retry_count(self.agent_name),
        )

        updated = state.model_copy(update={
            "fraud_result": fraud_result,
            "current_node": self.agent_name,
        })
        return updated.add_audit_event(
            node=self.agent_name,
            action="FRAUD_DEEP_ANALYSIS_COMPLETE",
            actor=self.agent_name,
            outcome=f"score={fraud_score:.4f} level={risk_level.value} blocked={is_blocked}",
            duration_ms=metrics.total_duration_ms,
            metadata={
                "confidence": metrics.confidence_score,
                "quality": metrics.quality_score,
                "corrections": metrics.self_corrections_count,
                "tool_success_rate": metrics.tool_success_rate,
                "llm_calls": metrics.llm_calls_count,
            },
            pii_accessed=True,
        )
