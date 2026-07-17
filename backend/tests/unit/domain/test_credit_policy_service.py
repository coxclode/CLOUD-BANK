"""Tests unitarios: CreditPolicyService — reglas de política crediticia."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.domain.entities.credit_application import CreditApplication, CreditPurpose
from src.domain.services.credit_policy_service import CreditPolicyService, PolicyEvaluation
from src.domain.value_objects.applicant import Applicant, EmploymentType
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore


POLICY = CreditPolicyService()


def _make_app(
    amount: float = 15_000.0,
    employment_months: int = 24,
) -> CreditApplication:
    applicant = Applicant.create(
        full_name="Ana López",
        national_id="0987654321",
        date_of_birth=date(1988, 3, 20),
        email="ana@example.com",
        phone="+57 310 000 0000",
    )
    return CreditApplication.create(
        applicant=applicant,
        requested_amount=Money(amount, "USD"),
        term_months=24,
        purpose=CreditPurpose.PERSONAL,
        channel="api",
        consent_given=True,
    )


def _low_fraud_risk() -> RiskScore:
    return RiskScore.compose(0.05, 0.10, 0.08, default_probability=0.08)


def _high_fraud_risk() -> RiskScore:
    return RiskScore.compose(0.90, 0.20, 0.20, default_probability=0.60)


class TestHardRules:

    def test_hard1_fraud_above_threshold_blocks(self):
        app = _make_app()
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.90,     # ≥ 0.85
            aml_check_passed=True,
            post_credit_dti=0.30,
            default_probability=0.10,
            active_applications_count=0,
        )
        assert not result.is_approved
        assert any("HARD-1" in v.rule_code for v in result.blocking_violations)

    def test_hard2_aml_failure_blocks(self):
        app = _make_app()
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.10,
            aml_check_passed=False,   # AML falló
            post_credit_dti=0.30,
            default_probability=0.10,
            active_applications_count=0,
        )
        assert not result.is_approved
        assert any("HARD-2" in v.rule_code for v in result.blocking_violations)

    def test_hard3_dti_above_50_blocks(self):
        app = _make_app()
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.10,
            aml_check_passed=True,
            post_credit_dti=0.55,     # > 0.50
            default_probability=0.10,
            active_applications_count=0,
        )
        assert not result.is_approved
        assert any("HARD-3" in v.rule_code for v in result.blocking_violations)

    def test_hard4_pd_above_70_blocks(self):
        app = _make_app()
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.10,
            aml_check_passed=True,
            post_credit_dti=0.30,
            default_probability=0.75,  # > 0.70
            active_applications_count=0,
        )
        assert not result.is_approved
        assert any("HARD-4" in v.rule_code for v in result.blocking_violations)

    def test_hard5_too_many_active_apps_blocks(self):
        app = _make_app()
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.10,
            aml_check_passed=True,
            post_credit_dti=0.30,
            default_probability=0.10,
            active_applications_count=3,  # > 2
        )
        assert not result.is_approved
        assert any("HARD-5" in v.rule_code for v in result.blocking_violations)

    def test_hard6_amount_above_500k_blocks(self):
        app = _make_app(amount=600_000.0)
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.10,
            aml_check_passed=True,
            post_credit_dti=0.30,
            default_probability=0.10,
            active_applications_count=0,
        )
        assert not result.is_approved
        assert any("HARD-6" in v.rule_code for v in result.blocking_violations)


class TestSoftRules:

    def test_good_profile_passes_all_rules(self):
        app = _make_app(amount=15_000.0)
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.05,
            aml_check_passed=True,
            post_credit_dti=0.30,
            default_probability=0.08,
            active_applications_count=0,
        )
        assert result.is_approved
        assert len(result.blocking_violations) == 0

    def test_multiple_hard_violations_all_reported(self):
        app = _make_app(amount=15_000.0)
        result = POLICY.evaluate_application(
            application=app,
            fraud_score=0.90,     # HARD-1
            aml_check_passed=False,  # HARD-2
            post_credit_dti=0.60, # HARD-3
            default_probability=0.80, # HARD-4
            active_applications_count=5, # HARD-5
        )
        assert not result.is_approved
        assert len(result.blocking_violations) >= 3
