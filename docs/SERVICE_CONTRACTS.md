# CLOUD BANK — Contratos entre Servicios

## Versión 1.0 | frontend ↔ backend ↔ ai-services

Este documento es la referencia de integración entre los 3 proyectos independientes
del repositorio (`frontend/`, `backend/`, `ai-services/`). Cada uno tiene su propio
manifiesto (`package.json` / `pyproject.toml`) y se ejecuta por separado; lo único
que los conecta es lo descrito aquí.

```
Usuario → Frontend (Next.js) → Backend API (FastAPI) → Orquestador LangGraph
                                                          (dentro de ai-services)
                                                       → Sistema Multiagente
                                                       → LLMs + Herramientas
                                     ↓
                               Base de datos (Postgres, solo backend)
                                     ↓
                          Respuesta al Frontend
```

**Regla de oro:** el frontend nunca ve `ai-services` ni la base de datos. Solo
conoce `BACKEND_URL`. El backend es el único que conoce `ai-services` y Postgres.

---

## 1. Frontend → Backend

Toda llamada sale de un Route Handler de Next.js (`frontend/app/api/**`), nunca
directamente del navegador — así `BACKEND_URL` y las credenciales nunca se exponen
al cliente.

### `POST /v1/auth/login`
```json
// Request
{ "username": "officer", "password": "..." }
// Response 200
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in_minutes": 60 }
```
El frontend guarda `access_token` en una cookie `httpOnly` (`cloudbank_session`).

### `POST /v1/credit/evaluate`
Header: `Authorization: Bearer <cloudbank_session>` (o `X-API-Key` para integraciones M2M).
```json
// Request — ver backend/src/application/dto/credit_application_dto.py
{
  "applicant": { "full_name": "...", "national_id": "...", "birth_date": "1990-01-01",
                 "email": "...", "phone": "+57...", "employment_type": "EMPLOYED",
                 "gross_monthly_income": 4500.0, "years_of_employment": 4,
                 "country_code": "CO", "city": "Bogotá" },
  "credit_request": { "requested_amount": 15000.0, "currency": "USD",
                       "term_months": 36, "purpose": "PERSONAL", "channel": "DIGITAL" },
  "consent_given": true
}
// Response 200 — ver CreditDecisionResponseDTO
{
  "decision_id": "...", "application_id": "...",
  "outcome": "APPROVED|REJECTED|MORE_DOCS|ESCALATED",
  "confidence": 0.87, "risk_band": "B", "risk_score": 640.0,
  "credit_terms": { "approved_amount": 15000.0, "interest_rate_annual": 18.5,
                     "monthly_installment": 542.10, ... } ,
  "rejection_reasons": [], "required_documents": [], "escalation": null,
  "justification": { "plain_language_explanation": "...", ... },
  "processing_time_ms": 812.4, "pipeline_id": "..."
}
```

### `GET /v1/credit/{application_id}` · `GET /v1/credit/{application_id}/decision`
Mismo esquema de auth. Devuelven estado/decisión ya persistidos.

---

## 2. Backend → ai-services

Único punto de acoplamiento: `backend/src/infrastructure/ai_services/ai_services_client.py`
implementa `OrchestratorPort` llamando por HTTP a ai-services. Configurado por
`CLOUDBANK_AI_SERVICES_URL` (ver `backend/.env.example`).

### `POST /v1/pipeline/evaluate`
```json
// Request
{
  "pipeline_id": "uuid",
  "application_id": "uuid",
  "application_data": {
    "national_id": "...", "full_name": "...", "gross_monthly_income": 4500.0,
    "requested_amount": 15000.0, "term_months": 36, "employment_type": "EMPLOYED",
    "...": "resto de campos serializados por AiServicesOrchestratorAdapter._serialize_application"
  }
}
// Response 200
{
  "fraud_result":     { "agent_name": "FraudDeepAgent", "outcome": "APPROVED|REJECTED|ESCALATED|REQUIRES_REVIEW|FAILED",
                         "confidence": 0.9, "quality_score": 0.8, "risk_contribution": 0.1,
                         "payload": { "fraud_score": 0.1, "...": "..." },
                         "reasoning_chain": [], "execution_time_ms": 142.3,
                         "human_review_required": false, "error_message": null },
  "credit_result":    { "...": "mismo esquema, agent_name=CreditDeepAgent" },
  "actuarial_result": { "...": "mismo esquema, agent_name=ActuarialDeepAgent" },
  "approval_result":  { "...": "mismo esquema, agent_name=ApprovalDeepAgent" },
  "fraud_score": 0.1, "aml_clear": true, "post_credit_dti": 0.32,
  "default_probability": 0.12, "suggested_rate": 18.5, "fraud_flags": [],
  "error": null
}
```

`outcome` es el vocabulario compartido (`ai-services/src/contracts/agent_result.py`
↔ `backend/src/application/ports/agent_port.py`) — **los valores deben coincidir
exactamente en ambos archivos** si alguno cambia.

### `GET /health`
Liveness simple (`{"status": "ok", "service": "ai-services"}`). El backend lo usa
en su propio `/health/ready` (`HealthChecker._check_ai_services`) — si ai-services
no responde, el backend se marca `degraded`, no `unhealthy`: sigue sirviendo
consultas de estado/decisión ya persistidas, solo degrada `/v1/credit/evaluate`.

### Degradación ante fallo
Si la llamada HTTP falla (timeout, conexión rechazada, 5xx), el adapter devuelve
un resultado degradado (`fraud_result=None`, `fraud_score=0.5`, `aml_clear=false`,
`error=<mensaje>`) en vez de propagar la excepción — el use case aplica la política
crediticia igual y normalmente escala a revisión humana.

---

## 3. Quién puede llamar a quién

| Origen | Destino permitido | Nunca permitido |
|---|---|---|
| Navegador | Frontend (Next.js, mismo origen) | Backend, ai-services, Postgres directamente |
| Frontend (server-side) | Backend (`BACKEND_URL`) | ai-services, Postgres |
| Backend | ai-services (`CLOUDBANK_AI_SERVICES_URL`), Postgres, Redis | — |
| ai-services | Proveedores LLM (Anthropic/OpenAI/Gemini), servicios externos (bureau/AML mock) | Postgres, Redis del backend |

Esto es lo que hace posible que `frontend/`, `backend/` y `ai-services/` se desplieguen,
escalen y fallen de forma independiente (ver `infrastructure/k8s/*-deployment.yaml`:
`ai-services` no tiene Ingress ni LoadBalancer, solo es alcanzable desde `backend`
vía `NetworkPolicy`).
