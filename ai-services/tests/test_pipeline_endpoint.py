"""
Smoke tests de ai-services. No requieren una API key real: con una key falsa,
los agentes fallan su llamada LLM y el pipeline degrada con gracia (outcome
FAILED por agente) en vez de tumbar el servicio — exactamente lo que debe
pasar ante un proveedor LLM no disponible.
"""

from __future__ import annotations


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_evaluate_pipeline_degrades_gracefully_without_real_llm(client):
    payload = {
        "pipeline_id": "test-pipeline-001",
        "application_id": "test-app-001",
        "application_data": {
            "application_id": "test-app-001",
            "national_id": "1234567890",
            "full_name": "Test Applicant",
            "gross_monthly_income": 4500.0,
            "requested_amount": 15000.0,
            "term_months": 36,
            "employment_type": "employed",
        },
    }
    response = client.post("/v1/pipeline/evaluate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "fraud_result" in body
    assert "approval_result" in body
