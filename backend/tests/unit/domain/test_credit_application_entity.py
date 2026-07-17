"""Tests unitarios: Aggregate Root CreditApplication."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from src.domain.entities.credit_application import (
    ApplicationStatus,
    CreditApplication,
    CreditApplicationError,
    CreditPurpose,
)
from src.domain.value_objects.applicant import Applicant
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore


def _make_applicant(**kwargs) -> Applicant:
    defaults = dict(
        full_name="Juan Pérez García",
        national_id="1234567890",
        date_of_birth=date(1985, 6, 15),
        email="juan@example.com",
        phone="+57 300 123 4567",
    )
    defaults.update(kwargs)
    return Applicant.create(**defaults)


def _make_application(**kwargs) -> CreditApplication:
    defaults = dict(
        applicant=_make_applicant(),
        requested_amount=Money(15_000.0, "USD"),
        term_months=36,
        purpose=CreditPurpose.PERSONAL,
        channel="api",
        consent_given=True,
    )
    defaults.update(kwargs)
    return CreditApplication.create(**defaults)


def _low_risk_score() -> RiskScore:
    return RiskScore.compose(0.05, 0.10, 0.08, default_probability=0.10)


class TestCreditApplicationCreation:

    def test_creates_in_draft_status(self):
        app = _make_application()
        assert app.status == ApplicationStatus.DRAFT

    def test_emits_created_event(self):
        app = _make_application()
        events = app.collect_events()
        assert len(events) == 1
        assert events[0].__class__.__name__ == "CreditApplicationCreated"

    def test_collect_events_clears_queue(self):
        app = _make_application()
        app.collect_events()
        assert app.collect_events() == []

    def test_rejects_no_consent(self):
        with pytest.raises((CreditApplicationError, ValueError)):
            _make_application(consent_given=False)


class TestCreditApplicationTransitions:

    def test_submit_moves_to_submitted(self):
        app = _make_application()
        app.submit()
        assert app.status == ApplicationStatus.SUBMITTED

    def test_start_review_moves_to_in_review(self):
        app = _make_application()
        app.submit()
        app.start_review()
        assert app.status == ApplicationStatus.IN_REVIEW

    def test_approve_moves_to_approved(self):
        app = _make_application()
        app.submit()
        app.start_review()
        app.approve(_low_risk_score())
        assert app.status == ApplicationStatus.APPROVED

    def test_reject_moves_to_rejected(self):
        app = _make_application()
        app.submit()
        app.start_review()
        app.reject(["Historial crediticio insuficiente"])
        assert app.status == ApplicationStatus.REJECTED

    def test_cannot_submit_twice(self):
        app = _make_application()
        app.submit()
        with pytest.raises(CreditApplicationError):
            app.submit()

    def test_cannot_approve_without_review(self):
        app = _make_application()
        app.submit()
        with pytest.raises(CreditApplicationError):
            app.approve(_low_risk_score())

    def test_cannot_transition_from_approved(self):
        app = _make_application()
        app.submit()
        app.start_review()
        app.approve(_low_risk_score())
        with pytest.raises(CreditApplicationError):
            app.reject(["reason"])

    def test_approved_status_is_terminal(self):
        assert ApplicationStatus.APPROVED.is_terminal

    def test_draft_status_is_not_terminal(self):
        assert not ApplicationStatus.DRAFT.is_terminal


class TestCreditApplicationDomainEvents:

    def test_submit_emits_submitted_event(self):
        app = _make_application()
        app.collect_events()  # clear created event
        app.submit()
        events = app.collect_events()
        assert any(e.__class__.__name__ == "CreditApplicationSubmitted" for e in events)

    def test_multiple_events_accumulate(self):
        app = _make_application()
        app.submit()
        app.start_review()
        events = app.collect_events()
        assert len(events) >= 2
