"""Tests unitarios: Value Object RiskScore."""

import pytest
from src.domain.value_objects.risk_score import RiskScore, RiskBand


class TestRiskScoreComposition:

    def test_low_risk_profile_maps_to_AA_band(self):
        score = RiskScore.compose(
            fraud_score=0.02, credit_score=0.05, actuarial_score=0.03,
            default_probability=0.03,
        )
        assert score.band in (RiskBand.AA, RiskBand.A)
        assert score.value > 850

    def test_high_risk_profile_maps_to_E_or_F_band(self):
        score = RiskScore.compose(
            fraud_score=0.80, credit_score=0.75, actuarial_score=0.70,
            default_probability=0.75,
        )
        assert score.band in (RiskBand.E, RiskBand.F)
        assert score.value < 350

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="pesos"):
            RiskScore.compose(
                fraud_score=0.1, credit_score=0.1, actuarial_score=0.1,
                default_probability=0.1,
                fraud_weight=0.5, credit_weight=0.5, actuarial_weight=0.5,
            )

    def test_value_stays_in_range(self):
        score = RiskScore.compose(0.0, 0.0, 0.0, default_probability=0.0)
        assert 0 <= score.value <= 1000


class TestRiskScoreProperties:

    def test_is_unacceptable_when_pd_above_threshold(self):
        score = RiskScore(
            value=400, fraud_component=0.0, credit_component=0.0,
            actuarial_component=0.0, default_probability=0.80,
        )
        assert score.is_unacceptable

    def test_is_acceptable_when_pd_below_threshold(self):
        score = RiskScore(
            value=750, fraud_component=0.0, credit_component=0.0,
            actuarial_component=0.0, default_probability=0.10,
        )
        assert not score.is_unacceptable

    def test_normalized_is_between_0_and_1(self):
        score = RiskScore(
            value=600, fraud_component=0.1, credit_component=0.2,
            actuarial_component=0.15, default_probability=0.2,
        )
        assert 0.0 <= score.normalized <= 1.0
        assert score.normalized == pytest.approx(0.6, abs=0.01)

    def test_investment_grade_bands(self):
        for band in (RiskBand.AA, RiskBand.A, RiskBand.B, RiskBand.C):
            assert band.is_investment_grade
        for band in (RiskBand.D, RiskBand.E, RiskBand.F):
            assert not band.is_investment_grade

    def test_value_out_of_range_raises(self):
        with pytest.raises(ValueError):
            RiskScore(
                value=1001,
                fraud_component=0.0, credit_component=0.0,
                actuarial_component=0.0, default_probability=0.0,
            )
