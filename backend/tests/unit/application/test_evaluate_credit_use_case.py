"""
Tests unitarios: EvaluateCreditApplicationUseCase

Estos tests verifican el flujo de orquestación sin I/O real.
Todos los repositorios, el orquestador y el publisher se mockean.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from src.application.ports.agent_port import AgentOutcome, AgentResult
from src.application.use_cases.evaluate_credit_application import EvaluateCreditApplicationUseCase
from src.domain.services.credit_policy_service import CreditPolicyService
from tests.factories import CreditApplicationDTOFactory


def _make_use_case(
    mock_app_repo,
    mock_decision_repo,
    mock_orchestrator,
    mock_event_publisher,
) -> EvaluateCreditApplicationUseCase:
    state_store = AsyncMock()
    state_store.save_state.return_value = None
    return EvaluateCreditApplicationUseCase(
        application_repo=mock_app_repo,
        decision_repo=mock_decision_repo,
        state_store=state_store,
        orchestrator=mock_orchestrator,
        policy_service=CreditPolicyService(),
        event_publisher=mock_event_publisher,
        notification_service=AsyncMock(),
    )


def _approved_pipeline_results() -> dict:
    return {
        "fraud": AgentResult(
            "FraudDeepAgent", AgentOutcome.APPROVED,
            confidence=0.95, quality_score=0.90, risk_contribution=0.05,
            payload={"fraud_score": 0.05, "is_blocked": False, "fraud_flags": [], "aml_clear": True},
            reasoning_chain=[], execution_time_ms=120.0,
        ),
        "credit": AgentResult(
            "CreditDeepAgent", AgentOutcome.APPROVED,
            confidence=0.80, quality_score=0.82, risk_contribution=0.15,
            payload={"probability_of_default": 0.12, "debt_to_income_ratio": 0.30, "composite_credit_score": 720},
            reasoning_chain=[], execution_time_ms=180.0,
        ),
        "actuarial": AgentResult(
            "ActuarialDeepAgent", AgentOutcome.APPROVED,
            confidence=0.85, quality_score=0.83, risk_contribution=0.12,
            payload={"loss_given_default": 0.12, "suggested_interest_rate": 0.12, "maximum_approved_amount": 15000.0, "risk_band": "B"},
            reasoning_chain=[], execution_time_ms=160.0,
        ),
        "approval": AgentResult(
            "ApprovalDeepAgent", AgentOutcome.APPROVED,
            confidence=0.90, quality_score=0.88, risk_contribution=0.10,
            payload={
                "decision": "APPROVED",
                "approved_amount": 15000.0,
                "interest_rate": 0.12,
                "term_months": 36,
                "monthly_installment": 498.21,
                "gdpr_explanation": "Solicitud aprobada.",
                "rejection_reasons": [],
            },
            reasoning_chain=[], execution_time_ms=90.0,
        ),
    }


class TestEvaluateCreditUseCaseApproval:

    @pytest.mark.asyncio
    async def test_happy_path_returns_approved_decision(
        self, mock_application_repo, mock_decision_repo,
        mock_orchestrator, mock_event_publisher,
    ):
        mock_orchestrator.run_evaluation_pipeline.return_value = _approved_pipeline_results()

        use_case = _make_use_case(
            mock_application_repo, mock_decision_repo,
            mock_orchestrator, mock_event_publisher,
        )
        application_data = CreditApplicationDTOFactory()
        result = await use_case.execute(application_data)

        assert result["decision"] == "APPROVED"
        assert result["approved_amount"] == 15000.0

    @pytest.mark.asyncio
    async def test_saves_application_to_repo(
        self, mock_application_repo, mock_decision_repo,
        mock_orchestrator, mock_event_publisher,
    ):
        mock_orchestrator.run_evaluation_pipeline.return_value = _approved_pipeline_results()

        use_case = _make_use_case(
            mock_application_repo, mock_decision_repo,
            mock_orchestrator, mock_event_publisher,
        )
        await use_case.execute(CreditApplicationDTOFactory())

        assert mock_application_repo.save.called

    @pytest.mark.asyncio
    async def test_publishes_domain_events(
        self, mock_application_repo, mock_decision_repo,
        mock_orchestrator, mock_event_publisher,
    ):
        mock_orchestrator.run_evaluation_pipeline.return_value = _approved_pipeline_results()

        use_case = _make_use_case(
            mock_application_repo, mock_decision_repo,
            mock_orchestrator, mock_event_publisher,
        )
        await use_case.execute(CreditApplicationDTOFactory())

        assert mock_event_publisher.publish.called


class TestEvaluateCreditUseCaseRejection:

    @pytest.mark.asyncio
    async def test_fraud_above_threshold_returns_rejected(
        self, mock_application_repo, mock_decision_repo,
        mock_orchestrator, mock_event_publisher,
    ):
        results = _approved_pipeline_results()
        results["fraud"] = AgentResult(
            "FraudDeepAgent", AgentOutcome.REJECTED,
            confidence=0.95, quality_score=0.90, risk_contribution=0.92,
            payload={"fraud_score": 0.92, "is_blocked": True, "fraud_flags": ["SYNTHETIC_ID"], "aml_clear": False},
            reasoning_chain=[], execution_time_ms=80.0,
        )
        results["approval"] = AgentResult(
            "ApprovalDeepAgent", AgentOutcome.REJECTED,
            confidence=0.99, quality_score=0.95, risk_contribution=0.92,
            payload={"decision": "REJECTED", "rejection_reasons": ["Fraude detectado"], "gdpr_explanation": "Solicitud rechazada por fraude.", "approved_amount": None},
            reasoning_chain=[], execution_time_ms=40.0,
        )
        mock_orchestrator.run_evaluation_pipeline.return_value = results

        use_case = _make_use_case(
            mock_application_repo, mock_decision_repo,
            mock_orchestrator, mock_event_publisher,
        )
        result = await use_case.execute(CreditApplicationDTOFactory())

        assert result["decision"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_active_applications_limit_blocks(
        self, mock_application_repo, mock_decision_repo,
        mock_orchestrator, mock_event_publisher,
    ):
        mock_application_repo.count_active_by_applicant.return_value = 3  # > 2

        use_case = _make_use_case(
            mock_application_repo, mock_decision_repo,
            mock_orchestrator, mock_event_publisher,
        )
        result = await use_case.execute(CreditApplicationDTOFactory())

        # Must reject without calling orchestrator (policy short-circuits)
        assert result["decision"] == "REJECTED"
        assert not mock_orchestrator.run_evaluation_pipeline.called
