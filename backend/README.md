# backend

API REST de CLOUD BANK — Clean Architecture (dominio / aplicación / infraestructura),
seguridad en capas (JWT + API Key, RBAC, rate limiting, prompt injection guard),
persistencia en PostgreSQL, cache/rate-limit en Redis.

Único componente que el frontend conoce. Único componente con acceso a la base
de datos. Delega toda evaluación con IA a `ai-services/` por HTTP — nunca
importa LangGraph, agentes ni SDKs de LLM directamente.

## Ejecutar de forma independiente

```bash
cp .env.example .env    # completar DATABASE_URL, REDIS_URL, JWT_SECRET, AI_SERVICES_URL
pip install -e ".[dev]"
uvicorn src.api.main:app --port 8000 --reload
```

## Endpoints principales

| Método | Ruta                          | Descripción                         |
|--------|-------------------------------|--------------------------------------|
| POST   | `/v1/auth/login`              | Login de oficial (JWT)                |
| POST   | `/v1/credit/evaluate`         | Evaluar solicitud de crédito          |
| GET    | `/v1/credit/{id}`             | Estado de una solicitud               |
| GET    | `/v1/credit/{id}/decision`    | Decisión de una solicitud             |
| GET    | `/v1/admin/health/live` `/v1/admin/health/ready` | Health checks (K8s probes, sin auth) |

Ver [`docs/SERVICE_CONTRACTS.md`](../docs/SERVICE_CONTRACTS.md) para el contrato completo.

## Tests

```bash
pytest tests/unit/ -m "not integration and not e2e" -v
pytest tests/integration/ -m integration -v   # requiere Redis + Postgres reales
pytest tests/e2e/ -m e2e -v
```
