"""
CLOUD BANK — Herramientas del Agente Antifraude
Wrappers sobre servicios externos: biometría, device intelligence, IP reputation.
Cada herramienta es independiente, tiene timeout propio y falla de forma aislada.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
from typing import Any

import httpx
import structlog

from src.core.state import (
    BiometricAnalysis,
    BehavioralSignals,
    DeviceIntelligence,
    DocumentVerification,
    IPIntelligence,
    SecurityContext,
)
from src.observability.metrics import record_agent_execution

logger = structlog.get_logger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


# ─────────────────────────────────────────────────────────────────────────────
# Verificación de documentos
# ─────────────────────────────────────────────────────────────────────────────

async def verify_document(
    document_references: list[str],
    national_id: str,
) -> DocumentVerification:
    """
    Verifica autenticidad de documentos contra el servicio de OCR + ML del banco.
    En producción: llama al microservicio de Document Intelligence.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            service_url = os.environ.get("BIOMETRIC_SERVICE_URL", "http://localhost:9001")
            response = await client.post(
                f"{service_url}/api/v1/document/verify",
                json={
                    "documents": document_references,
                    "national_id_hash": hashlib.sha256(national_id.encode()).hexdigest(),
                },
                headers={"Authorization": f"Bearer {os.environ.get('BIOMETRIC_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()
            return DocumentVerification(
                document_type=data.get("document_type", "national_id"),
                is_authentic=data.get("is_authentic", True),
                confidence=float(data.get("confidence", 0.85)),
                tamper_indicators=data.get("tamper_indicators", []),
                ocr_consistency_score=float(data.get("ocr_consistency_score", 0.90)),
                metadata_integrity=bool(data.get("metadata_integrity", True)),
            )
    except (httpx.HTTPError, Exception) as e:
        logger.warning("verify_document.service_unavailable", error=str(e))
        # Fallback: evaluar por número y tipo de documentos disponibles
        has_docs = len(document_references) > 0
        return DocumentVerification(
            document_type="national_id",
            is_authentic=has_docs,
            confidence=0.60 if has_docs else 0.30,
            tamper_indicators=[] if has_docs else ["NO_DOCUMENTS_PROVIDED"],
            ocr_consistency_score=0.70 if has_docs else 0.20,
            metadata_integrity=has_docs,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Análisis biométrico
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_biometrics(
    biometric_token: str | None,
    national_id: str,
) -> BiometricAnalysis:
    """
    Verifica liveness, face match y deepfake contra el servicio biométrico.
    """
    if not biometric_token:
        return BiometricAnalysis(
            liveness_score=0.0,
            face_match_score=0.0,
            deepfake_probability=0.0,
            spoofing_detected=False,
            biometric_flags=["NO_BIOMETRIC_TOKEN"],
        )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            service_url = os.environ.get("BIOMETRIC_SERVICE_URL", "http://localhost:9001")
            response = await client.post(
                f"{service_url}/api/v1/biometric/verify",
                json={
                    "token": biometric_token,
                    "id_hash": hashlib.sha256(national_id.encode()).hexdigest(),
                },
                headers={"Authorization": f"Bearer {os.environ.get('BIOMETRIC_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()
            return BiometricAnalysis(
                liveness_score=float(data.get("liveness_score", 0.0)),
                face_match_score=float(data.get("face_match_score", 0.0)),
                deepfake_probability=float(data.get("deepfake_probability", 0.0)),
                spoofing_detected=bool(data.get("spoofing_detected", False)),
                biometric_flags=data.get("flags", []),
            )
    except (httpx.HTTPError, Exception) as e:
        logger.warning("analyze_biometrics.service_unavailable", error=str(e))
        return BiometricAnalysis(
            liveness_score=0.5,
            face_match_score=0.5,
            deepfake_probability=0.1,
            spoofing_detected=False,
            biometric_flags=["BIOMETRIC_SERVICE_UNAVAILABLE"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Señales comportamentales
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_behavioral_signals(
    security_context: SecurityContext,
) -> BehavioralSignals:
    """
    Analiza metadatos de comportamiento del usuario durante el llenado del formulario.
    Los datos vienen del frontend (session recording anonimizado).
    """
    # En producción: endpoint de análisis de comportamiento en tiempo real
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            service_url = os.environ.get("DEVICE_INTELLIGENCE_URL", "http://localhost:9002")
            response = await client.post(
                f"{service_url}/api/v1/behavior/analyze",
                json={"session_id": security_context.session_id},
                headers={"Authorization": f"Bearer {os.environ.get('DEVICE_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()
            return BehavioralSignals(
                typing_pattern_anomaly=float(data.get("typing_anomaly", 0.0)),
                navigation_pattern_anomaly=float(data.get("navigation_anomaly", 0.0)),
                time_on_fields_anomaly=float(data.get("time_anomaly", 0.0)),
                copy_paste_detected=bool(data.get("copy_paste", False)),
                auto_fill_detected=bool(data.get("auto_fill", False)),
                bot_probability=float(data.get("bot_probability", 0.0)),
            )
    except Exception as e:
        logger.warning("analyze_behavioral_signals.unavailable", error=str(e))
        return BehavioralSignals(
            typing_pattern_anomaly=0.1,
            navigation_pattern_anomaly=0.1,
            time_on_fields_anomaly=0.1,
            bot_probability=0.1,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Device Intelligence
# ─────────────────────────────────────────────────────────────────────────────

async def check_device_intelligence(
    device_fingerprint: str | None,
    ip_address: str,
) -> DeviceIntelligence:
    """
    Verifica la reputación del dispositivo: emuladores, dispositivos rooteados,
    asociaciones con fraudes previos.
    """
    if not device_fingerprint:
        return DeviceIntelligence(
            device_flags=["NO_DEVICE_FINGERPRINT"],
            device_reputation_score=0.5,
        )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            service_url = os.environ.get("DEVICE_INTELLIGENCE_URL", "http://localhost:9002")
            response = await client.post(
                f"{service_url}/api/v1/device/check",
                json={"fingerprint": device_fingerprint, "ip": ip_address},
                headers={"Authorization": f"Bearer {os.environ.get('DEVICE_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()
            return DeviceIntelligence(
                device_id=data.get("device_id", ""),
                is_emulator=bool(data.get("is_emulator", False)),
                is_rooted=bool(data.get("is_rooted", False)),
                device_reputation_score=float(data.get("reputation_score", 0.8)),
                previous_fraud_associations=int(data.get("fraud_count", 0)),
                device_flags=data.get("flags", []),
            )
    except Exception as e:
        logger.warning("check_device_intelligence.unavailable", error=str(e))
        return DeviceIntelligence(
            device_reputation_score=0.6,
            device_flags=["DEVICE_SERVICE_UNAVAILABLE"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# IP Reputation
# ─────────────────────────────────────────────────────────────────────────────

async def check_ip_reputation(
    ip_address: str,
    security_context: SecurityContext,
) -> IPIntelligence:
    """
    Verifica reputación de la IP: proxies, Tor, datacenters, historial de fraudes.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            service_url = os.environ.get("IP_REPUTATION_URL", "http://localhost:9003")
            response = await client.get(
                f"{service_url}/api/v1/ip/{ip_address}",
                headers={"Authorization": f"Bearer {os.environ.get('IP_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()
            return IPIntelligence(
                ip_address=ip_address,
                reputation_score=float(data.get("reputation_score", 0.8)),
                is_proxy=bool(data.get("is_proxy", security_context.is_vpn)),
                is_vpn=bool(data.get("is_vpn", security_context.is_vpn)),
                is_tor=bool(data.get("is_tor", security_context.is_tor)),
                is_datacenter=bool(data.get("is_datacenter", security_context.is_datacenter_ip)),
                country=data.get("country", security_context.geo_country),
                previous_fraud_count=int(data.get("fraud_count", 0)),
                ip_flags=data.get("flags", []),
            )
    except Exception as e:
        logger.warning("check_ip_reputation.unavailable", error=str(e))
        return IPIntelligence(
            ip_address=ip_address,
            reputation_score=0.7,
            is_vpn=security_context.is_vpn,
            is_tor=security_context.is_tor,
            is_datacenter=security_context.is_datacenter_ip,
            country=security_context.geo_country,
            ip_flags=["IP_SERVICE_UNAVAILABLE"],
        )
