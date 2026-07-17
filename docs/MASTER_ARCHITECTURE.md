# CLOUD BANK — Arquitectura Maestra del Sistema

## Sistema Multi-Agente de Evaluación de Crédito Personal  
**Versión 3.0 | Microservicios Desacoplados | Clean Architecture | Producción-Ready**

---

## 0. Arquitectura de Alto Nivel: 3 Proyectos Independientes

El sistema está dividido en 3 proyectos que se ejecutan, versionan y despliegan
por separado — cada uno con su propio manifiesto (`package.json` / `pyproject.toml`)
y su propio `Dockerfile`. La única comunicación entre ellos es HTTP/REST, nunca
imports de código compartido. Ver el contrato exacto en `docs/SERVICE_CONTRACTS.md`.

```
frontend/       Next.js 14 + TypeScript + Tailwind — nunca toca DB ni LLM
                 └─► solo consume la API REST de backend/

backend/        FastAPI — Clean Architecture (dominio/aplicación/infraestructura)
                 └─► único componente con acceso a Postgres y Redis
                 └─► llama a ai-services/ por HTTP para toda evaluación con IA

ai-services/    FastAPI delgado + LangGraph + 4 Deep Agents + LLM multi-proveedor
                 └─► servicio interno, sin Ingress: solo backend/ lo alcanza
                 └─► nunca toca la base de datos

infrastructure/ docker-compose.yml (orquesta los 3 en local) + k8s/ + prometheus.yml
docs/           Este documento + FREE_TIER_ARCHITECTURE.md + SERVICE_CONTRACTS.md
```

```
Usuario → Frontend (Next.js) → Backend API (FastAPI) → Orquestador LangGraph
                                                          (dentro de ai-services)
                                                       → Sistema Multiagente
                                                       → LLMs + Herramientas
                                     ↓
                               Base de datos (solo backend)
                                     ↓
                          Respuesta al Frontend
```

Las secciones §1–§12 de este documento describen el diseño interno de **backend/**
y **ai-services/** combinados (el "Clean Architecture" original), que sigue siendo
válido: la única diferencia es que la frontera entre la capa de orquestación
(`orchestrator/`, `agents/`) y la capa de aplicación (`domain/`, `application/`,
`infrastructure/`) — antes una llamada de función in-process — ahora es una
llamada HTTP entre dos servicios desplegados por separado.

---

## 1. Estructura del Proyecto (dentro de backend/ + ai-services/)

```
CLOUD BANK/
├── src/
│   ├── domain/                     ← Núcleo del negocio. Sin dependencias externas.
│   │   ├── entities/               ← Aggregates + Entities con identidad
│   │   │   ├── credit_application.py   Aggregate Root: toda la lógica de una solicitud
│   │   │   └── credit_decision.py      Decision inmutable: APPROVED/REJECTED/ESCALATED
│   │   ├── value_objects/          ← Objetos por valor: inmutables, definidos por atributos
│   │   │   ├── money.py                Money(amount, currency) con redondeo bancario
│   │   │   ├── risk_score.py           RiskScore compuesto (bandas AA→F, Basel III)
│   │   │   └── applicant.py            Applicant validado (email, phone, national_id)
│   │   ├── events/                 ← Domain Events: hechos inmutables del dominio
│   │   │   └── credit_events.py        9 eventos (Created, Submitted, Approved, etc.)
│   │   ├── repositories/           ← Interfaces abstractas (Ports del dominio)
│   │   │   └── credit_repository.py    CreditApplicationRepository, CreditDecisionRepository
│   │   └── services/               ← Servicios de dominio: reglas que no pertenecen a entidades
│   │       └── credit_policy_service.py  6 reglas HARD + 4 SOFT (Basel III + Política Interna)
│   │
│   ├── application/                ← Casos de uso. Orquesta el dominio.
│   │   ├── use_cases/              ← Un archivo por caso de uso
│   │   │   ├── evaluate_credit_application.py  Pipeline de 9 pasos (caso principal)
│   │   │   └── retrieve_credit_decision.py     Consulta de decisión existente
│   │   ├── ports/                  ← Interfaces que la aplicación define y la infraestructura implementa
│   │   │   ├── agent_port.py           AgentPort + AgentResult (contrato de los agentes)
│   │   │   └── secret_port.py          SecretManagerPort (abstracción de secretos)
│   │   └── dtos/                   ← Data Transfer Objects (entrada/salida de casos de uso)
│   │
│   ├── infrastructure/             ← Implementaciones concretas. Depende de application/domain.
│   │   ├── persistence/
│   │   │   ├── postgres_repositories.py  asyncpg: CreditApplication + CreditDecision
│   │   │   └── redis_state_store.py      LangGraph checkpoint store (24h TTL)
│   │   ├── secrets/
│   │   │   └── vault_client.py           HashiCorp Vault KV v2 + cache 5min + fallback env
│   │   └── messaging/
│   │       └── event_publisher.py        Redis Streams XADD (at-least-once, maxlen=100k)
│   │
│   ├── agents/                     ← Deep Agents autónomos. Cada uno es independiente.
│   │   ├── deep/                   ← Implementaciones de 10 capas de razonamiento
│   │   │   ├── base_deep_agent.py      Pipeline base L1→L10 (compartido por todos)
│   │   │   ├── schemas.py              Contratos tipados entre capas (Pydantic strict)
│   │   │   ├── fraud_deep_agent.py     Antifraude: biometría, dispositivo, comportamiento
│   │   │   ├── credit_deep_agent.py    Historial crediticio: bureau, capacidad, DTI
│   │   │   ├── actuarial_deep_agent.py Riesgo actuarial: PD, LGD, EAD, banda de riesgo
│   │   │   └── approval_deep_agent.py  Aprobación final: reglas duras + términos crediticios
│   │   └── adapters/               ← Patrón Adapter: DeepAgent → AgentPort
│   │       ├── fraud_adapter.py        Convierte CreditEvaluationState ↔ application_data
│   │       ├── credit_adapter.py
│   │       ├── actuarial_adapter.py
│   │       └── approval_adapter.py
│   │
│   ├── orchestrator/               ← LangGraph StateGraph. Orquesta los 4 agentes.
│   │   ├── graph/
│   │   │   └── credit_evaluation_graph.py  LangGraphOrchestrator con MemorySaver
│   │   ├── state/
│   │   │   └── evaluation_state.py         TypedDict EvaluationState
│   │   ├── nodes/
│   │   │   └── agent_execution_node.py     BaseAgentNode: timeout 60s + retry + métricas
│   │   └── edges/
│   │       └── routing_logic.py            Circuit breaker fraude: score≥0.90 → AUDIT
│   │
│   ├── security/                   ← Capa transversal de seguridad.
│   │   ├── authentication/
│   │   │   └── api_key_authenticator.py  HMAC-SHA256 timing-safe, no almacena texto plano
│   │   ├── authorization/
│   │   │   └── rbac.py                   RBAC: Permission enum + Role → frozenset[Permission]
│   │   ├── encryption/
│   │   │   └── field_encryptor.py        Fernet AES-256 para PII en reposo
│   │   ├── rate_limiting/
│   │   │   └── rate_limiter.py           Sliding Window distribuido en Redis
│   │   └── guards/
│   │       └── prompt_injection_guard.py 30+ patrones injection + 15 jailbreak + Unicode
│   │
│   ├── monitoring/                 ← Observabilidad completa.
│   │   ├── logging/
│   │   │   └── structured_logger.py  structlog JSON + redacción automática de PII
│   │   ├── metrics/
│   │   │   └── prometheus_registry.py 20+ métricas custom (no global registry)
│   │   ├── tracing/
│   │   │   └── tracer.py             OpenTelemetry traces con contextvars async
│   │   ├── auditing/
│   │   │   └── audit_service.py      AuditRecord inmutable con checksum SHA-256
│   │   └── health/
│   │       └── health_checker.py     Liveness/Readiness con asyncio.gather paralelo
│   │
│   └── api/                        ← Capa HTTP. FastAPI.
│       ├── app.py                  create_app() factory con lifespan + middlewares
│       ├── dependencies.py         Composition Root: DI wiring con @lru_cache singletons
│       ├── middleware/
│       │   ├── auth_middleware.py          Autenticación en cada request
│       │   ├── rate_limit_middleware.py    Rate limiting por IP/API Key
│       │   └── security_headers_middleware.py HSTS, CSP, X-Frame-Options
│       └── v1/
│           ├── credit_router.py    POST /evaluate, GET /status, GET /decision
│           └── admin_router.py     GET /health, GET /metrics, GET /audit
│
├── config/
│   ├── settings.py         8 clases Pydantic Settings con env var prefixes
│   └── logging_config.yaml structlog + handlers (console/file) + PII fields
│
├── tests/
│   ├── conftest.py         Fixtures globales: FakeRedis, TestClient, mocks de repos
│   ├── factories.py        factory_boy: CreditApplicationDTOFactory, HighRiskFactory
│   ├── unit/               Tests sin I/O. Veloces. Sin fixtures pesadas.
│   │   ├── domain/         Entidades, Value Objects, Policy Service
│   │   ├── application/    Use cases con repositorios mockeados
│   │   └── security/       Rate limiter con FakeRedis
│   ├── integration/        Tests con Redis/Postgres reales (pytest -m integration)
│   └── e2e/               Tests HTTP completos contra TestClient (pytest -m e2e)
│
├── docker/
│   ├── Dockerfile          Multi-stage: builder → runtime (imagen mínima)
│   └── docker-compose.yml  Stack local: API + Redis + Postgres + Jaeger + Prometheus
│
├── k8s/
│   ├── deployment.yaml     Kubernetes Deployment con health probes
│   ├── service.yaml        ClusterIP + LoadBalancer
│   └── hpa.yaml            HPA: 2-10 réplicas, CPU 70% / RPS 100
│
└── .github/
    └── workflows/
        └── ci.yml          Lint → Unit → Integration → Security → Docker → Release Gate
```

---

## 2. Regla de Dependencias (Clean Architecture)

```
                ┌─────────────────────────┐
                │      Infrastructure      │  asyncpg, Redis, Vault, HTTP clients
                │   (implementaciones)     │
                └────────────┬────────────┘
                             │ implementa
                ┌────────────▼────────────┐
                │       Application        │  Use Cases, Ports (interfaces)
                │   (casos de uso)         │
                └────────────┬────────────┘
                             │ usa
                ┌────────────▼────────────┐
                │         Domain           │  Entities, Value Objects, Domain Events
                │   (núcleo del negocio)   │  Cero dependencias externas
                └─────────────────────────┘

REGLA FUNDAMENTAL: Las capas internas NUNCA importan las externas.
  Domain     → no importa nada del proyecto
  Application → solo importa Domain
  Infrastructure → implementa las interfaces de Application
  API         → depende de Application (Composition Root inyecta Infrastructure)
```

---

## 3. Flujo de Ejecución Completo

```
Cliente HTTP
     │
     ▼
SecurityHeadersMiddleware   → HSTS, CSP, X-Frame-Options en cada respuesta
     │
     ▼
RateLimitMiddleware         → Sliding Window en Redis (por IP + por API Key)
     │
     ▼
AuthMiddleware              → HMAC-SHA256 timing-safe API Key validation
     │
     ▼
CORSMiddleware              → Validación de origen
     │
     ▼
POST /v1/credit/evaluate
     │
     ▼
PromptInjectionGuard.scan() → Escaneo de 30+ patrones en todos los strings del payload
     │
     ▼
EvaluateCreditApplicationUseCase.execute()
     │
     ├─► [1] Construir entidades de dominio desde DTO
     │       Applicant.create() → valida email, phone, national_id
     │       Money(amount, currency) → redondeo bancario
     │       CreditApplication.create() → valida invariantes de dominio
     │
     ├─► [2] Verificar solicitudes activas (CreditApplicationRepository)
     │       count_active_by_applicant() → si > 2, REJECT (HARD-5)
     │
     ├─► [3] Persistir + emitir eventos
     │       application_repo.save()
     │       event_publisher.publish(CreditApplicationSubmitted)
     │
     ├─► [4] Ejecutar pipeline de agentes (OrchestratorPort → LangGraphOrchestrator)
     │        │
     │        ▼ StateGraph(EvaluationState)
     │        │
     │        ├─► InputValidationNode → validación de esquema final
     │        │
     │        ├─► FraudExecutionNode
     │        │       FraudDeepAgentAdapter.execute()
     │        │         → build CreditEvaluationState
     │        │         → FraudDeepAgent.run(state)
     │        │              L1  Validación entrada + seguridad
     │        │              L2  Validación contexto (dependencias entre agentes)
     │        │              L3  Plan adaptativo (FULL_PARALLEL vs GROUPED)
     │        │              L4  Herramientas paralelas: biometría, dispositivo, IP, AML, bureau
     │        │              L5  Verificación cross (consistencia entre herramientas)
     │        │              L6  Razonamiento LLM con system prompt endurecido
     │        │              L7  Auto-corrección iterativa (máx 3 rounds)
     │        │              L8  Quality Assessment (4 dimensiones ponderadas)
     │        │              L9  Justificación regulatoria (GDPR + Basel III)
     │        │              L10 Ensamblaje + métricas + auditoría
     │        │
     │        ├─► RoutingEdge: si fraud_score ≥ 0.90 → AUDIT (circuit breaker)
     │        │
     │        ├─► CreditExecutionNode  (mismas 10 capas)
     │        ├─► ActuarialExecutionNode (mismas 10 capas)
     │        ├─► ApprovalExecutionNode (mismas 10 capas)
     │        │
     │        └─► AuditFinalizationNode → registro de auditoría inmutable
     │
     ├─► [5] Evaluar política crediticia
     │       CreditPolicyService.evaluate_application()
     │         HARD-1: fraud ≥ 0.85 → REJECT
     │         HARD-2: AML positivo → REJECT
     │         HARD-3: DTI > 50% → REJECT
     │         HARD-4: PD > 70% → REJECT
     │         HARD-5: solicitudes activas > 2 → REJECT
     │         HARD-6: monto > 500.000 → REJECT
     │         SOFT-1: monto > 50.000 → ESCALATE a comité
     │         SOFT-2: PD ∈ (45%, 70%] → ESCALATE borderline
     │         SOFT-3: banda E/F → requiere garantía
     │         SOFT-4: empleo < 6 meses → requiere garante
     │
     ├─► [6] Construir CreditDecision (inmutable)
     │       CreditDecision.approve() / .reject() / .escalate()
     │       Incluye: términos crediticios, cuota mensual (amortización francesa),
     │       score de riesgo (AA→F), explicación GDPR Art. 22
     │
     ├─► [7] Actualizar estado de la solicitud
     │       application.approve(risk_score) / .reject(reasons) / .escalate()
     │       application_repo.save()
     │
     ├─► [8] Notificar solicitante
     │       notification_service.notify() (async, no bloquea)
     │
     └─► [9] Retornar DTO de respuesta
             decision, approved_amount, interest_rate, monthly_installment,
             gdpr_explanation, request_id, processing_time_ms
     │
     ▼
AuditService.create()       → AuditRecord con checksum SHA-256 (inmutable, verificable)
     │
     ▼
Response JSON               + X-Request-ID, X-Response-Time, Security Headers
```

---

## 4. Diseño Interno de los Deep Agents

Cada agente implementa un **pipeline de 10 capas independientes**. Cada capa produce un resultado tipado (Pydantic) que la siguiente consume. Ningún estado se pasa como `dict` libre.

```
CAPA  NOMBRE                    FUNCIÓN
────  ────────────────────────  ──────────────────────────────────────────────────
L1    Input Validation          Seguridad: injection, jailbreak, poisoning, Unicode
                                Schema: campos requeridos, tipos, rangos
                                PII: clasificación y logging seguro
L2    Context Validation        Dependencias: resultados de agentes anteriores presentes
                                Consistencia: no hay contradicciones entre inputs
L3    Planning                  Estrategia adaptativa: FULL_PARALLEL / GROUPED / SEQUENTIAL
                                Decisión basada en complejidad y dependencias de herramientas
L4    Tool Execution            Paralelo con asyncio.gather + timeout individual por tool
                                Retry con backoff exponencial
                                Fallback a datos sintéticos si tool falla
L5    Verification              Cross-validation entre resultados de herramientas
                                Detección de anomalías estadísticas
                                Flags de inconsistencia (LOW/MEDIUM/HIGH/CRITICAL)
L6    Reasoning (LLM)           Prompt endurecido con system prompt anti-injection
                                Análisis principal con todos los datos verificados
                                Output estructurado (JSON forzado, no texto libre)
L7    Self-Correction           Iterativa (máx 3 rounds)
                                Detecta violaciones de reglas de negocio
                                Re-llama al LLM solo con las violaciones identificadas
L8    Quality Assessment        Score 0.0-1.0 por 4 dimensiones ponderadas
                                Threshold mínimo (0.55) — si falla, degrada a REQUIRES_REVIEW
L9    Justification             Explicabilidad regulatoria:
                                  - GDPR Art. 22: explicación en lenguaje natural
                                  - Basel III: referencias normativas
                                  - Factual counterfactual: "si X fuera distinto"
L10   Output Assembly           Estado final + métricas de ejecución + registro de auditoría
                                Trazabilidad completa: request_id → pipeline_id → agent_id
```

### Circuit Breaker de Fraude

```python
def route_after_fraud(state: EvaluationState) -> NodeName:
    if state["fraud_score"] >= 0.90:
        # Fraude crítico → saltamos Credit y Actuarial → ahorro de 2 LLM calls
        return NodeName.AUDIT
    return NodeName.CREDIT
```

---

## 5. Seguridad en Profundidad (Defense in Depth)

### 5.1 Capas de Protección

```
CAPA 1: RED
  ✓ TLS 1.3 terminado en el Load Balancer (Nginx / Cloud LB)
  ✓ TrustedHostMiddleware: rechaza requests a hosts no autorizados
  ✓ IP allowlisting en nivel de Kubernetes NetworkPolicy

CAPA 2: TRANSPORTE HTTP
  ✓ HSTS (Strict-Transport-Security: max-age=31536000; includeSubDomains)
  ✓ Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
  ✓ X-Content-Type-Options: nosniff
  ✓ X-Frame-Options: DENY
  ✓ Referrer-Policy: no-referrer
  ✓ Permissions-Policy: geolocation=(), camera=(), microphone=()

CAPA 3: AUTENTICACIÓN
  ✓ API Key HMAC-SHA256 (timing-safe con hmac.compare_digest)
  ✓ No se almacena el API key en texto plano (solo el hash)
  ✓ Prefijo indexado (8 chars) para lookup eficiente sin exponer el key

CAPA 4: AUTORIZACIÓN
  ✓ RBAC: Permission enum → Role → frozenset[Permission]
  ✓ Zero Trust: cada endpoint verifica permisos explícitos
  ✓ Roles: ANALYST (read-only), OFFICER (evaluate), ADMIN (full)

CAPA 5: RATE LIMITING
  ✓ Sliding Window en Redis (distribuido → funciona con múltiples pods)
  ✓ 60 req/min por defecto, 10 req/min para /evaluate
  ✓ Responde con X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After

CAPA 6: VALIDACIÓN DE INPUT
  ✓ Pydantic v2 strict mode en todos los DTOs
  ✓ PromptInjectionGuard: escaneo recursivo de todos los strings
      - 30+ patrones de injection (ignore instructions, system:, [INST], etc.)
      - 15+ patrones de jailbreak (DAN, roleplay evil, developer mode, etc.)
      - 20+ patrones de data poisoning (prompt leaking, exfiltration)
      - Unicode attacks: RTL override, zero-width chars, homoglyph detection
  ✓ Sanitización: NFKC normalization + strip control chars

CAPA 7: CIFRADO DE PII
  ✓ Fernet AES-256 para national_id, email, phone en reposo (PostgreSQL)
  ✓ Redacción automática en logs (structlog PII processor)
  ✓ Vault para gestión de claves de cifrado

CAPA 8: SECRETS MANAGEMENT
  ✓ HashiCorp Vault KV v2 como fuente primaria
  ✓ Cache local de 5 minutos (evita latencia por llamada a Vault)
  ✓ Fallback a env vars para desarrollo local
  ✓ Nunca se persisten secrets en código ni en logs
```

### 5.2 Amenazas y Mitigaciones Específicas

| Amenaza | Mitigación |
|---------|------------|
| Prompt Injection | PromptInjectionGuard L1 + system prompt endurecido en LLM |
| Timing Attack (auth) | `hmac.compare_digest` — tiempo constante |
| DoS / Brute Force | Rate limiter Redis + Circuit Breaker |
| SQL Injection | asyncpg parameterized queries (sin concatenación) |
| Credential Exposure | Vault + env var fallback + `SecretStr` (Pydantic) |
| Data Exfiltration via LLM | Output parsing estricto + JSON schema enforcement |
| PII en Logs | structlog processor redacta campos PII en cada evento |
| Audit Tampering | SHA-256 checksum en cada AuditRecord (frozen dataclass) |
| Replay Attack | X-Request-ID UUID único + Redis dedup (TTL 5min) |

---

## 6. Estrategia de Tests

### 6.1 Pirámide de Tests

```
          ╔══════════╗
          ║   E2E    ║  ~20 tests — TestClient HTTP — pytest -m e2e
          ╠══════════╣  Contrato HTTP completo, headers de seguridad
          ║Integration║  ~30 tests — Redis/Postgres reales — pytest -m integration
          ╠══════════╣  Rate limiter, state store, persistencia
          ║   Unit   ║  ~100+ tests — FakeRedis, mocks — pytest -m unit
          ╚══════════╝  Domain, Value Objects, Policy, Use Cases, Security
```

### 6.2 Ejecución

```bash
# Solo tests unitarios (rápido, sin infraestructura)
pytest tests/unit/ -m "not integration and not e2e" -v

# Tests de integración (requiere Redis + Postgres)
pytest tests/integration/ -m integration -v

# E2E con stack completo
pytest tests/e2e/ -m e2e -v

# Coverage completa
pytest tests/unit/ --cov=src --cov-report=html --cov-fail-under=80

# Todos los tests
pytest -v
```

### 6.3 Herramientas

| Herramienta | Uso |
|-------------|-----|
| `pytest-asyncio` | Tests async sin boilerplate |
| `fakeredis` | Redis en memoria para unit tests |
| `factory_boy` | Generación reproducible de datos de prueba |
| `respx` | Mock de HTTP clients (bureaus externos) |
| `freezegun` | Control de tiempo en tests de rate limiting |
| `pytest-cov` | Coverage con umbral mínimo 80% |
| `pytest-mock` | `AsyncMock` para repositorios y agentes |

---

## 7. Observabilidad

### 7.1 Logging (structlog)

```python
# Cada log event incluye automáticamente:
{
  "timestamp": "2026-06-26T10:00:00.123Z",
  "level": "INFO",
  "event": "cloudbank.credit_evaluation.completed",
  "request_id": "abc-123",      # propagado por contextvars
  "application_id": "app-456",
  "pipeline_id": "pip-789",
  "agent": "FraudDeepAgent",
  "elapsed_ms": 142.3,
  # PII automáticamente redactado:
  "national_id": "[REDACTED]",
  "email": "[REDACTED]",
  "phone": "[REDACTED]"
}
```

### 7.2 Métricas (Prometheus)

```
# Disponibles en GET /metrics (Bearer admin)

cloudbank_http_requests_total{method, endpoint, status_code}
cloudbank_http_request_duration_seconds{method, endpoint, status_code}
cloudbank_pipeline_duration_seconds{outcome}
cloudbank_agent_execution_duration_seconds{agent_name, outcome}
cloudbank_fraud_score_distribution (Histogram)
cloudbank_default_probability_distribution (Histogram)
cloudbank_credit_decisions_total{outcome, risk_band}
cloudbank_active_evaluations (Gauge)
cloudbank_security_violations_total{violation_type}
cloudbank_rate_limit_hits_total{endpoint}
cloudbank_llm_calls_total{agent, model, status}
cloudbank_llm_call_duration_seconds{agent, model}
```

### 7.3 Tracing (OpenTelemetry)

```
Span: POST /v1/credit/evaluate
  ├── Span: use_case.evaluate
  │     ├── Span: policy.check_active_apps
  │     ├── Span: orchestrator.run_pipeline
  │     │     ├── Span: agent.fraud.L1-L10 (60-200ms)
  │     │     ├── Span: agent.credit.L1-L10 (80-250ms)
  │     │     ├── Span: agent.actuarial.L1-L10 (70-200ms)
  │     │     └── Span: agent.approval.L1-L10 (40-120ms)
  │     └── Span: decision.persist
  └── Total: 300-800ms (p95)
```

### 7.4 Auditoría

Cada decisión crediticia genera un `AuditRecord` **inmutable** con:
- `checksum`: SHA-256 del payload serializado (detecta tampering)
- `action`: CREATE / EVALUATE / APPROVE / REJECT / ESCALATE
- `actor`: ID del officer o "system" para decisiones automáticas
- `pii_accessed`: flag booleano (cumplimiento GDPR Art. 30)
- `data_classification`: CONFIDENTIAL / RESTRICTED / PUBLIC
- `verify_integrity()`: recomputa y compara checksum — detecta modificaciones

---

## 8. Estrategia de Despliegue

### 8.1 Kubernetes (Producción)

```yaml
# Recursos por pod (calibrados para carga media)
resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "2000m"
    memory: "2Gi"

# HPA: escala entre 2 y 10 réplicas
# Trigger: CPU > 70% ó RPS > 100 por pod
# Cooldown: 5 min para scale-down (evitar flapping)

# Health Probes
livenessProbe:   GET /health/live  → 200 siempre
readinessProbe:  GET /health/ready → 200 solo si Redis + Postgres healthy
```

### 8.2 Pipeline de Despliegue

```
git push → GitHub Actions CI
  1. Lint (ruff + mypy + bandit)          ~1 min
  2. Unit Tests (pytest + coverage)       ~2 min
  3. Integration Tests (Redis + Postgres) ~3 min
  4. Security Scan (pip-audit + safety)   ~1 min
  5. Docker Build (multi-stage)           ~3 min
  6. Release Gate (todos los jobs verde)
  7. Deploy a Staging → smoke tests
  8. Deploy a Producción (blue/green)
```

### 8.3 Docker Multi-Stage

```dockerfile
# Stage 1: Builder (incluye herramientas de build)
FROM python:3.12-slim AS builder
RUN pip install hatchling
COPY pyproject.toml .
RUN pip wheel --no-deps --wheel-dir /wheels .

# Stage 2: Runtime (imagen mínima)
FROM python:3.12-slim AS runtime
# Sin pip, sin gcc, sin tools — superficie de ataque mínima
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels cloudbank-credit-engine
USER cloudbank  # No root
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 9. Estrategia de Escalabilidad

### 9.1 Escalado Horizontal (Stateless API)

```
Load Balancer (Nginx / AWS ALB)
       │
       ├─► Pod 1: API (sin estado local)
       ├─► Pod 2: API (sin estado local)
       └─► Pod N: API (sin estado local)
              │
              ├─► Redis Cluster (shared state: rate limits, LangGraph checkpoints)
              └─► PostgreSQL (RDS Multi-AZ / Cloud SQL HA)
```

**Por qué funciona:** el rate limiter usa Redis (no memoria local), el LangGraph checkpoint usa Redis, los repositorios usan PostgreSQL. Cualquier pod puede manejar cualquier request.

### 9.2 Escalado de Carga LLM

| Técnica | Implementación |
|---------|----------------|
| Caché semántica | Redis con embedding similarity — reutiliza resultados para inputs similares |
| Batching | Agrupar solicitudes de baja prioridad en ventanas de 100ms |
| Modelo degradado | Si el modelo principal está saturado, usar modelo más pequeño con flag de "revisión humana" |
| Circuit breaker | Si el LLM devuelve errores >5% en 1 min, activar fallback de reglas determinísticas |

### 9.3 Database Scaling

```
Read Replicas: consultas de status/decisión → read replica (PostgreSQL)
Write Primary: solo escrituras de nuevas solicitudes/decisiones
Connection Pooling: PgBouncer entre API y PostgreSQL (pool de 20 conexiones por pod)
Índices críticos: application_id, applicant_id, status, created_at
Particionamiento: por mes en tabla de auditoría (> 1M registros/mes)
```

---

## 10. Estrategia de Reducción de Costos

### 10.1 Costos LLM (Principal gasto operativo)

| Optimización | Ahorro Estimado |
|--------------|-----------------|
| Circuit breaker fraude (score≥0.90 → skip Credit+Actuarial) | 40% en casos de fraude |
| Caché semántica de resultados similares | 20-30% del total |
| Modelo más pequeño para L1-L5 (validación) | 40% costo de tokens de preparación |
| Batch de solicitudes no urgentes | 15% por descuento de batch API |
| Self-correction limitada a máx 3 rounds | Previene loops infinitos |

**Monitorear:** `cloudbank_llm_calls_total` y `cloudbank_llm_call_duration_seconds` para detectar regresiones de costo.

### 10.2 Costos de Infraestructura

```
Redis: Elasticache t3.small para dev, r6g.large para prod (cluster mode)
PostgreSQL: RDS db.t3.medium para dev, db.r6g.large Multi-AZ para prod
Kubernetes: Spot instances para pods de CPU (no críticos), On-Demand para DB
Logs: Retención 30 días en S3 Standard, 1 año en S3 Glacier
Métricas: Prometheus retención 15 días local, Thanos para largo plazo
```

### 10.3 Cost Observability

```python
# Cada llamada LLM registra tokens y costo estimado
CLOUDBANK_LLM_TOKENS_TOTAL{agent, model, direction}  # input/output
CLOUDBANK_LLM_COST_USD_TOTAL{agent, model}           # estimado en USD
```

Alertar si costo/hora supera umbral definido en `config/settings.py`.

---

## 11. Mejores Prácticas Implementadas

### Código

- **Immutability first**: `@dataclass(frozen=True)` para Value Objects y Domain Events
- **Fail fast**: validaciones en el constructor, no en métodos de negocio
- **No magic strings**: enums para estados, outcomes, permisos, bandas de riesgo
- **Type safety**: Python 3.12 `from __future__ import annotations`, mypy strict
- **No global state**: `lru_cache` para singletons controlados, `contextvars` para async context

### Seguridad

- **Secrets**: nunca en código, siempre en Vault/env. `SecretStr` de Pydantic en config.
- **PII**: cifrado en reposo (Fernet), redactado en logs, flag de acceso en auditoría
- **Input**: validar en el perímetro (API), confiar internamente (no re-validar en domain)
- **Auth**: HMAC timing-safe, sin JWT stateless (no permite revocación inmediata)

### Operaciones

- **Health checks**: liveness separada de readiness — Kubernetes no mata pods por dependencias lentas
- **Graceful shutdown**: lifespan context manager cierra conexiones antes de terminar
- **Configuration as code**: `pyproject.toml` define toda la toolchain (ruff, mypy, pytest, coverage)
- **Immutable audit trail**: SHA-256 checksum verificable en post-incident forensics

---

## 12. Glosario

| Término | Definición |
|---------|------------|
| Aggregate Root | Entidad que garantiza las invariantes de un grupo de objetos relacionados |
| Domain Event | Hecho inmutable que ocurrió en el dominio (pasado, no comando) |
| Value Object | Objeto definido por sus atributos, sin identidad propia, inmutable |
| Port | Interfaz abstracta que define un contrato entre capas |
| Adapter | Implementación concreta de un Port |
| PD | Probability of Default: probabilidad de incumplimiento en 12 meses |
| LGD | Loss Given Default: porcentaje de pérdida si hay incumplimiento |
| DTI | Debt-to-Income Ratio: obligaciones mensuales / ingreso mensual |
| Basel III | Marco regulatorio bancario internacional de gestión de riesgo |
| GDPR Art. 22 | Derecho a no ser sujeto de decisiones automatizadas sin explicación |
| Circuit Breaker | Patrón que corta el flujo ante condiciones de error para ahorrar recursos |
| HPA | Horizontal Pod Autoscaler: escalado automático en Kubernetes |
| AML | Anti-Money Laundering: verificación contra listas de lavado de dinero |
