"""
Nodo: Validación de entrada del pipeline.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.orchestrator.state.evaluation_state import (
    EvaluationState, NodeName, PipelineError, PipelineStatus,
)

logger = structlog.get_logger(__name__)


class InputValidationNode:
    """Valida que el estado inicial tenga todos los datos requeridos."""

    REQUIRED_FIELDS = [
        "application_id", "pipeline_id", "application_data",
    ]

    REQUIRED_APPLICATION_FIELDS = [
        "national_id", "gross_monthly_income", "requested_amount",
        "term_months", "employment_type",
    ]

    async def execute(self, state: EvaluationState) -> dict[str, Any]:
        logger.info("input_validation_node.starting", pipeline_id=state.get("pipeline_id"))
        errors = []

        for field in self.REQUIRED_FIELDS:
            if not state.get(field):
                errors.append(PipelineError(
                    node=NodeName.VALIDATE_INPUT,
                    error_type="MISSING_FIELD",
                    message=f"Campo obligatorio ausente en el estado: '{field}'",
                    is_fatal=True,
                    timestamp=0.0,
                ))

        app_data = state.get("application_data", {})
        for field in self.REQUIRED_APPLICATION_FIELDS:
            if field not in app_data:
                errors.append(PipelineError(
                    node=NodeName.VALIDATE_INPUT,
                    error_type="MISSING_APPLICATION_FIELD",
                    message=f"Campo obligatorio ausente en application_data: '{field}'",
                    is_fatal=True,
                    timestamp=0.0,
                ))

        if errors:
            logger.error(
                "input_validation_node.failed",
                errors=[e.message for e in errors],
            )
            return {
                "current_node":  NodeName.VALIDATE_INPUT,
                "pipeline_status": PipelineStatus.FAILED,
                "is_fatal_error": True,
                "errors": list(state.get("errors", [])) + errors,
            }

        logger.info("input_validation_node.passed", pipeline_id=state.get("pipeline_id"))
        return {
            "current_node": NodeName.VALIDATE_INPUT,
            "pipeline_status": PipelineStatus.RUNNING,
            "is_fatal_error": False,
            "node_durations": {**(state.get("node_durations") or {}), "validate_input": 0.5},
        }
