"""
Guard: Prompt Injection + Jailbreak + Data Poisoning

Primera línea de defensa antes de que cualquier dato del usuario
llegue al LLM. Escanea TODOS los campos de texto de la solicitud.

Diseño: fail-secure — si hay duda, bloquear.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ThreatCategory(str, Enum):
    PROMPT_INJECTION = "PROMPT_INJECTION"
    JAILBREAK        = "JAILBREAK"
    DATA_POISONING   = "DATA_POISONING"
    UNICODE_ATTACK   = "UNICODE_ATTACK"
    ENCODING_ATTACK  = "ENCODING_ATTACK"


@dataclass
class ThreatDetection:
    threat_type: ThreatCategory
    matched_pattern: str
    field_name: str
    severity: str


class PromptInjectionError(Exception):
    def __init__(self, detections: list[ThreatDetection]) -> None:
        super().__init__(f"Amenaza detectada: {[d.threat_type.value for d in detections]}")
        self.detections = detections


class PromptInjectionGuard:
    """
    Escanea inputs en busca de ataques contra el LLM.
    Se ejecuta en L1 del pipeline Deep Agent y en la validación de la API.
    """

    _INJECTION_PATTERNS = [
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        r"disregard\s+(?:all\s+)?(?:previous|prior|above)",
        r"new\s+instructions?\s*:",
        r"system\s*prompt\s*:",
        r"<\s*/?system\s*>",
        r"<\s*/?instruction\s*>",
        r"<\s*/?prompt\s*>",
        r"\[INST\]",
        r"\[/INST\]",
        r"###\s*instruction",
        r"you\s+are\s+now\s+(?:a\s+)?(?:different|new|another|an?\s+AI)",
        r"act\s+as\s+(?:if\s+)?(?:you\s+are\s+)?(?:a\s+)?(?:different|new)",
        r"roleplay\s+as",
        r"pretend\s+(?:you\s+are|to\s+be)",
        r"simulate\s+(?:being\s+)?(?:a\s+)?(?:different|new)",
        r"bypass\s+(?:your\s+)?(?:safety|security|filter|restriction|guideline)",
        r"override\s+(?:your\s+)?(?:safety|security|filter|restriction|guideline)",
        r"<!--.*?-->",
        r"\/\*.*?\*\/",
        r"\{\{.*?\}\}",
        r"__import__",
        r"eval\s*\(",
        r"exec\s*\(",
        r"os\.system",
        r"subprocess\.",
        r"\bSELECT\b.+\bFROM\b",
        r"\bDROP\s+TABLE\b",
        r"\bINSERT\s+INTO\b",
        r"\bUNION\s+(?:ALL\s+)?SELECT\b",
        r"<script[^>]*>",
        r"javascript:",
        r"on(?:click|load|error|mouseover)\s*=",
    ]

    _JAILBREAK_PATTERNS = [
        r"DAN\s+mode",
        r"developer\s+mode",
        r"god\s+mode",
        r"jailbreak",
        r"uncensored",
        r"without\s+(?:any\s+)?restrictions?",
        r"no\s+(?:moral|ethical)\s+(?:guidelines|constraints)",
        r"evil\s+(?:AI|assistant|mode)",
        r"do\s+anything\s+now",
        r"DUDE\s+mode",
        r"AIM\s+mode",
    ]

    _DATA_POISONING_PATTERNS = [
        r"fraud\s*(?:score|level)\s*(?:is|=|:)\s*(?:0|zero|null|none)",
        r"approved?\s+(?:automatically|unconditionally|always)",
        r"risk\s*(?:score|level)\s*(?:is|=|:)\s*(?:0|zero|null|none|minimal)",
        r"no\s+(?:fraud|risk|issue|problem)\s+(?:found|detected)",
        r"trust\s+(?:this|the)\s+(?:user|customer|applicant|request)",
        r"verified\s+by\s+(?:the\s+)?(?:system|bank|admin)",
        r"approved?\s+by\s+(?:the\s+)?(?:system|bank|manager|admin)",
        r"credit\s+score\s*(?:is|=|:)\s*(?:9\d{2}|1000|perfect|excellent)",
    ]

    _UNICODE_ATTACK_CHARS = [
        "‮",  # Right-to-Left Override
        "​",  # Zero Width Space
        "­",  # Soft Hyphen
        "﻿",  # BOM
        " ",  # Line Separator
        " ",  # Paragraph Separator
    ]

    def __init__(self) -> None:
        self._injection_res = [
            re.compile(p, re.IGNORECASE | re.DOTALL) for p in self._INJECTION_PATTERNS
        ]
        self._jailbreak_res = [
            re.compile(p, re.IGNORECASE) for p in self._JAILBREAK_PATTERNS
        ]
        self._poisoning_res = [
            re.compile(p, re.IGNORECASE) for p in self._DATA_POISONING_PATTERNS
        ]

    def scan_dict(self, data: dict[str, Any], context: str = "input") -> list[ThreatDetection]:
        """Escanea todos los valores de texto de un dict."""
        detections: list[ThreatDetection] = []
        for key, value in data.items():
            if isinstance(value, str):
                detections.extend(self.scan_text(value, field_name=f"{context}.{key}"))
            elif isinstance(value, dict):
                detections.extend(self.scan_dict(value, context=f"{context}.{key}"))
        return detections

    def scan_and_raise(self, data: dict[str, Any]) -> None:
        """Escanea y lanza PromptInjectionError si hay amenazas."""
        detections = self.scan_dict(data)
        if detections:
            logger.warning(
                "prompt_injection_guard.threat_detected",
                threats=[d.threat_type.value for d in detections],
                fields=[d.field_name for d in detections],
            )
            raise PromptInjectionError(detections)

    def scan_text(self, text: str, field_name: str = "unknown") -> list[ThreatDetection]:
        detections: list[ThreatDetection] = []
        if not text:
            return detections

        normalized = self._normalize(text)

        for pattern in self._injection_res:
            if pattern.search(normalized):
                detections.append(ThreatDetection(
                    threat_type=ThreatCategory.PROMPT_INJECTION,
                    matched_pattern=pattern.pattern[:40],
                    field_name=field_name,
                    severity="CRITICAL",
                ))
                break

        for pattern in self._jailbreak_res:
            if pattern.search(normalized):
                detections.append(ThreatDetection(
                    threat_type=ThreatCategory.JAILBREAK,
                    matched_pattern=pattern.pattern[:40],
                    field_name=field_name,
                    severity="HIGH",
                ))
                break

        for pattern in self._poisoning_res:
            if pattern.search(normalized):
                detections.append(ThreatDetection(
                    threat_type=ThreatCategory.DATA_POISONING,
                    matched_pattern=pattern.pattern[:40],
                    field_name=field_name,
                    severity="HIGH",
                ))
                break

        for char in self._UNICODE_ATTACK_CHARS:
            if char in text:
                detections.append(ThreatDetection(
                    threat_type=ThreatCategory.UNICODE_ATTACK,
                    matched_pattern=repr(char),
                    field_name=field_name,
                    severity="MEDIUM",
                ))
                break

        return detections

    def sanitize(self, text: str) -> str:
        """
        Sanitización defensiva: normaliza Unicode y elimina caracteres de control.
        Usar DESPUÉS de scan — si scan no detecta amenaza, sanitize igual es buena práctica.
        """
        normalized = unicodedata.normalize("NFKC", text)
        cleaned = "".join(
            ch for ch in normalized
            if not unicodedata.category(ch).startswith("C") or ch in ("\n", "\t", " ")
        )
        for char in self._UNICODE_ATTACK_CHARS:
            cleaned = cleaned.replace(char, "")
        return cleaned.strip()

    @staticmethod
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text).lower()
