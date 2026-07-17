# CLOUD BANK — Arquitectura Académica Free-Tier (Fase 1)

## Versión 1.0 | Complementa a `MASTER_ARCHITECTURE.md` (Fase 2 — Producción Empresarial)

---

## 0. Contexto y decisión de alcance

> **Actualización:** el repo ya se reestructuró en 3 proyectos independientes —
> `frontend/`, `backend/`, `ai-services/` — más `infrastructure/` y `docs/` (ver
> `docs/MASTER_ARCHITECTURE.md §0` y `docs/SERVICE_CONTRACTS.md`). Todo lo que
> este documento decía sobre "el backend en `src/`" ahora se reparte entre
> `backend/` (dominio, aplicación, infraestructura, seguridad, API) y
> `ai-services/` (LangGraph, Deep Agents, LLM). Las decisiones de free-tier de
> abajo siguen aplicando igual — solo cambia que ahora son **dos** servicios
> desplegables en vez de uno (ver §1.1 y §8 actualizados).

El backend ya construido (Clean Architecture: `domain` → `application` → `infrastructure`,
4 Deep Agents sobre LangGraph, seguridad en capas) fue diseñado asumiendo infraestructura de
producción real: Kong, HashiCorp Vault, Istio/mTLS, Kubernetes con HPA, RDS Multi-AZ,
ElastiCache r6g.large, stack ELK + Jaeger. Ese diseño **no es Free Tier** y no debe usarse
para el desarrollo académico.

**Decisión:** mantener el núcleo (`domain/`, `application/`, `agents/`, `orchestrator/`,
`security/`) sin cambios — porque ya está aislado del mundo exterior mediante **Ports & Adapters**
— y sustituir únicamente los *adapters* de infraestructura por implementaciones que apuntan a
servicios gratuitos. Esto es exactamente lo que el patrón Hexagonal permite sin reescribir
lógica de negocio.

`MASTER_ARCHITECTURE.md` queda como el **plano de destino** (Fase 2, migración empresarial, §11).
Este documento es la **Fase 1**: cómo desplegar el mismo sistema, más un frontend Next.js, a coste
≈ $0/mes en planes gratuitos, con una ruta de migración 1:1 hacia la Fase 2.

---

## 1. Stack Tecnológico — Decisión Final

| Capa | Tecnología elegida | Sustituye a (Fase 2) | Por qué |
|---|---|---|---|
| Frontend | Next.js 14 (App Router) + React + TypeScript + Tailwind | — (nuevo) | SSR/ISR gratis en Vercel, DX estándar de industria |
| Backend | FastAPI + Pydantic v2 + Uvicorn | — (ya existe) | Ya implementado; async nativo, tipado fuerte |
| Orquestación agentes | LangGraph + LangChain (solo en capa de tools/parsing) | — (ya existe) | Ya implementado; StateGraph con checkpointing |
| Base de datos | **Neon PostgreSQL (Free)** | RDS Multi-AZ | Serverless, branching gratis, autosuspend, 0.5 GB free |
| Cache / estado LangGraph | **Upstash Redis (Free)** | ElastiCache | REST + TCP, serverless, 256 MB / 500K comandos-mes gratis |
| Mensajería async | **QStash (Upstash, Free)** para webhooks/reintentos; **Redis Streams** (ya implementado) se mantiene para eventos internos | RabbitMQ / MSK | QStash = HTTP-based, sin servidor que mantener; RabbitMQ solo local via Docker Compose para desarrollo |
| Almacenamiento | **Supabase Storage (Free)** o Cloudinary Free (docs/imágenes de KYC) | S3 | 1 GB gratis Supabase, integrado con Postgres del mismo proveedor si se usa Supabase completo |
| Auth | **JWT propio (ya implementado) + OAuth2** para login de oficiales; Clerk Free opcional para el frontend si se requiere UI de auth lista | Vault + IdP empresarial | Ya hay `api_key_authenticator.py` HMAC; se añade JWT de sesión para el panel web |
| Secrets | **GitHub Secrets** (CI) + variables de entorno del hosting (Render/Railway/Vercel) | HashiCorp Vault | Vault requiere un servidor propio — no gratis de mantener 24/7 |
| LLM | **Capa de abstracción multi-proveedor**: Anthropic Claude / OpenAI / Gemini intercambiables | — | Ver §5. Evita vendor lock-in y permite usar tiers gratuitos de cada proveedor en desarrollo |
| Observabilidad | **Grafana Cloud Free** (10k métricas, 50 GB logs) + OpenTelemetry SDK (ya implementado) | Prometheus+Grafana self-hosted, Jaeger, ELK | Grafana Cloud Free acepta OTLP directo — no hace falta correr Prometheus/Jaeger propios |
| Contenedores | Docker + Docker Compose (dev local, ya implementado) | Kubernetes + HPA | K8s no es necesario a esta escala; Render/Fly.io ya autoescalan |
| CI/CD | GitHub Actions (Free, 2000 min/mes en repos privados, ilimitado en públicos) | — (ya implementado en `.github/workflows/ci.yml`) | Sin cambios |
| Hosting backend | **Render (Free/Starter) o Fly.io (Free allowance)** | Kubernetes / EKS / Cloud Run | Ver comparativa §1.1 |
| Hosting frontend | **Vercel (Hobby, Free)** | — | Estándar de facto para Next.js |

### 1.1 Elección de hosting backend: Render vs Fly.io vs Railway vs Cloud Run

| Criterio | Render Free | Fly.io Free | Railway | Cloud Run Free |
|---|---|---|---|---|
| Sleep tras inactividad | Sí (~15 min) | No (con 1 shared-cpu-1x) | No, pero $5 crédito/mes se agota rápido | No (escala a 0, cold start rápido) |
| WebSockets / long-lived conns | Sí | Sí | Sí | Limitado (timeout 60 min) |
| Región cercana a Neon/Upstash | Oregon/Frankfurt | Múltiples | Múltiples | Múltiples |
| Facilidad Docker Compose→deploy | Alta (usa el `Dockerfile` de cada proyecto) | Alta (`fly.toml`) | Alta | Media (requiere Cloud Build) |
| **Elegido** | ✅ **backend + ai-services** | Alternativa/failover | — | Alternativa si se prefiere GCP |

**Recomendación:** Render Free para el MVP académico — **dos servicios Render separados**,
uno por `backend/Dockerfile` y otro por `ai-services/Dockerfile` (cada uno con su propio
plan free, $0 los dos). `ai-services` no necesita dominio público: solo `CLOUDBANK_AI_SERVICES_URL`
en el backend apuntando a la URL interna/privada del segundo servicio. Trade-off honesto: dos
servicios free-tier con sleep independiente significan que ambos pueden tener cold-start
simultáneo tras inactividad (~30-50s cada uno la primera vez). Si eso es un problema para la
demo, usar Fly.io (sin sleep) para al menos `ai-services` — ambos usan el mismo contenedor,
no hay lock-in. El frontend (`frontend/Dockerfile`) va aparte, en Vercel.

---

## 2. Arquitectura Lógica

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              CLIENTE (Browser)                            │
└──────────────────────────────────┬────────────────────────────────────────┘
                                    │ HTTPS (TLS 1.3, gestionado por Vercel)
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    FRONTEND — Next.js 14 (Vercel Free)                    │
│  App Router · Server Components · Tailwind · React Query                  │
│  - /login            (JWT / Clerk)                                        │
│  - /dashboard         Panel de solicitudes (officer/analyst)               │
│  - /apply             Formulario de solicitud de crédito                  │
│  - /api/proxy/*       Route Handlers → reenvían a backend (oculta API key)│
└──────────────────────────────────┬────────────────────────────────────────┘
                                    │ HTTPS + Bearer JWT / X-API-Key
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                 BACKEND — FastAPI (Render/Fly.io Free)                    │
│                                                                             │
│  Middlewares: SecurityHeaders → RateLimit(Upstash) → Auth → CORS          │
│  PromptInjectionGuard → EvaluateCreditApplicationUseCase                   │
│                                                                             │
│  ┌────────────────────── LangGraph StateGraph ─────────────────────────┐  │
│  │ validate_input → security_check → FRAUD → CREDIT → ACTUARIAL →      │  │
│  │ APPROVAL → audit_finalize   (circuit breaker fraude ≥0.90 → AUDIT)  │  │
│  │                                                                       │  │
│  │        Cada agente → LLM Provider Abstraction Layer (§5)            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└───────┬──────────────────────┬──────────────────────┬─────────────────────┘
        │                      │                      │
        ▼                      ▼                      ▼
┌───────────────┐    ┌──────────────────┐    ┌───────────────────────────┐
│ Neon Postgres  │    │  Upstash Redis    │    │  LLM Providers (HTTPS)   │
│ (Free 0.5 GB)  │    │  (Free 256 MB)    │    │  Anthropic / OpenAI /    │
│ - applications │    │  - rate limit     │    │  Gemini (abstraídos)     │
│ - decisions    │    │  - LangGraph      │    │                          │
│ - audit trail  │    │    checkpoints    │    └───────────────────────────┘
└───────────────┘    │  - QStash queue   │
                      └──────────────────┘
                              │
                              ▼
                 ┌─────────────────────────────┐
                 │ Supabase Storage / Cloudinary│  (documentos KYC, PDFs)
                 └─────────────────────────────┘

Observabilidad (transversal, todos los componentes emiten OTLP):
        FastAPI/OTel SDK ──OTLP/HTTPS──► Grafana Cloud Free (Tempo+Loki+Prometheus)
```

---

## 3. Arquitectura Física / Diagrama de Despliegue

```
                              Internet (usuarios / oficiales de crédito)
                                            │
                                            │ HTTPS
                    ┌───────────────────────┴────────────────────────┐
                    ▼                                                ▼
        ┌───────────────────────┐                     ┌───────────────────────────┐
        │   VERCEL (Frontend)    │                     │  GRAFANA CLOUD (Free)      │
        │  Next.js — Edge CDN    │                     │  Dashboards / Alertas      │
        │  Región: auto (global) │                     └──────────────┬─────────────┘
        └───────────┬───────────┘                                     │ OTLP HTTPS
                    │ HTTPS (fetch server-side)                       │
                    ▼                                                 │
        ┌───────────────────────────────────────────────────┐         │
        │      RENDER (Free/Starter Web Service)              │◄──────┘
        │      Región: Oregon (us-west) o Frankfurt            │
        │  ┌─────────────────────────────────────────────┐   │
        │  │ Contenedor Docker (docker/Dockerfile)         │   │
        │  │  uvicorn src.api.main:app  (1 réplica Free)   │   │
        │  │  - No root, imagen multi-stage                │   │
        │  └─────────────────────────────────────────────┘   │
        └──────┬───────────────────────┬──────────────────────┘
               │ TCP/TLS 5432           │ TCP/TLS 6379 + HTTPS REST
               ▼                       ▼
   ┌─────────────────────────┐  ┌─────────────────────────────┐
   │  NEON (Postgres Free)    │  │  UPSTASH (Redis + QStash)    │
   │  Región: us-east-2       │  │  Región: más cercana a Render │
   │  Autosuspend tras 5 min  │  │  Global (edge caching)        │
   │  Branching para tests    │  └─────────────────────────────┘
   └─────────────────────────┘
               │
               ▼
   ┌─────────────────────────┐
   │ SUPABASE STORAGE (Free)  │  documentos KYC / PDFs de contrato
   └─────────────────────────┘

   ┌─────────────────────────────────────────────────────────┐
   │  GITHUB (repo coxclode/CLOUD-BANK)                        │
   │  - GitHub Actions: lint → test → build → deploy hook      │
   │  - GitHub Secrets: NEON_URL, UPSTASH_*, LLM_API_KEY, JWT_* │
   │  - Webhook de deploy → Render / Vercel (auto en push a main)│
   └─────────────────────────────────────────────────────────┘
```

**Nota de disponibilidad:** en el tier gratuito, Render duerme el servicio tras ~15 min sin
tráfico (cold start ~30-50s en el siguiente request). Para una demo académica esto es aceptable;
si se requiere alta disponibilidad real, migrar a Render Starter ($7/mes) o Fly.io (sin sleep)
es un cambio de una sola variable de entorno de despliegue, no de código.

---

## 4. Flujo de Red

```
1. Usuario → Vercel Edge (TLS 1.3 terminado por Vercel, HSTS activo)
2. Vercel Route Handler (server-side, oculta la API key) → Render (HTTPS, TLS 1.3)
3. Render → FastAPI: SecurityHeadersMiddleware, luego RateLimitMiddleware
      → Upstash Redis (REST API sobre HTTPS, sliding window por IP/API-Key)
4. AuthMiddleware valida JWT (oficiales) o API Key HMAC (integraciones)
5. PromptInjectionGuard escanea el payload (in-process, sin red)
6. Use case → Neon Postgres (TLS obligatorio, `sslmode=require`) — INSERT solicitud
7. LangGraph StateGraph ejecuta agentes:
      → Cada Deep Agent llama al LLM Provider Layer (HTTPS saliente a
        api.anthropic.com / api.openai.com / generativelanguage.googleapis.com,
        según `CLOUDBANK_LLM_PROVIDER`)
      → Checkpoints de estado → Upstash Redis (TCP TLS o REST)
8. Circuit breaker de fraude: si score≥0.90, se corta el flujo (sin más llamadas LLM)
9. Decisión final → Neon Postgres (UPDATE) + AuditRecord (INSERT, checksum SHA-256)
10. Documentos adjuntos (si existen) → Supabase Storage (HTTPS, URL firmada, TTL 1h)
11. Todas las llamadas emiten spans/métricas OTLP → Grafana Cloud (HTTPS, batch cada 5s)
12. Respuesta JSON → Vercel → Usuario (headers de seguridad + X-Request-ID)

Todo el tráfico externo es HTTPS/TLS 1.3. No hay tráfico interno en texto plano:
Render, Neon, Upstash y Supabase exponen únicamente endpoints TLS incluso en el tier free.
```

---

## 5. Capa de Abstracción del Proveedor LLM

Actualmente `config/settings.py::LLMSettings` y `langchain-anthropic` están acoplados a
Anthropic. Se propone una interfaz `LLMProviderPort` en `src/application/ports/`:

```python
# src/application/ports/llm_provider_port.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LLMResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str

class LLMProviderPort(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> LLMResponse: ...
```

```python
# src/infrastructure/llm/provider_factory.py
def get_llm_provider(settings: LLMSettings) -> LLMProviderPort:
    match settings.provider:
        case "anthropic": return AnthropicAdapter(settings)   # langchain-anthropic / anthropic SDK
        case "openai":    return OpenAIAdapter(settings)      # openai SDK
        case "gemini":    return GeminiAdapter(settings)      # google-generativeai SDK
        case _: raise ValueError(f"Proveedor LLM no soportado: {settings.provider}")
```

- `CLOUDBANK_LLM_PROVIDER=anthropic|openai|gemini` cambia el proveedor **sin tocar** `agents/deep/*`.
- Cada agente (`fraud_deep_agent.py`, etc.) depende de `LLMProviderPort`, no del SDK concreto.
- Permite usar el free tier de cada proveedor en desarrollo (créditos gratuitos de Gemini/OpenAI)
  y reservar Anthropic para producción, sin reescribir la capa L6 (Reasoning) del pipeline.
- Métrica ya prevista en `MASTER_ARCHITECTURE.md §10.1`: `cloudbank_llm_calls_total{agent, model, status}`
  se etiqueta también con `provider` para comparar coste/latencia entre proveedores.

---

## 6. Seguridad Zero Trust en el Tier Gratuito

Todo lo ya implementado en `src/security/` se mantiene intacto (HMAC auth, RBAC, rate limiting,
PromptInjectionGuard, Fernet encryption). Los ajustes son solo de **dónde viven los secretos**:

| Control | Implementación Fase 1 (Free) |
|---|---|
| Secrets management | GitHub Secrets (CI) + env vars nativas de Render/Vercel (cifradas at-rest por el proveedor) |
| Cifrado en tránsito | TLS 1.3 impuesto por Vercel, Render, Neon y Upstash — no requiere configuración |
| Cifrado en reposo (PII) | Fernet AES-256 (ya implementado) — clave desde env var, rotación manual documentada |
| Rate limiting | Sliding window ya implementado, backend = Upstash Redis (mismo cliente `redis-py`, solo cambia la URL) |
| Prompt Injection / Jailbreak | `prompt_injection_guard.py` sin cambios (in-process, no depende de infra) |
| Auth oficiales (frontend) | JWT (ya en `SecuritySettings.jwt_secret`) + OAuth2 opcional vía Clerk Free (login social) |
| Auditoría | AuditRecord + checksum SHA-256 en Neon Postgres (misma tabla, mismo código) |
| WAF / DDoS básico | Incluido gratis en el CDN de Vercel (frontend) y en el proxy de Render (backend) |

**Limitación honesta:** no hay Vault, ni mTLS entre servicios, ni Kubernetes NetworkPolicy en este
tier. Es un trade-off aceptado explícitamente para entorno académico — documentado como brecha a
cerrar en la migración (§11).

---

## 7. Observabilidad en Free Tier

- El código ya emite OTLP (`src/observability/tracer.py`, `metrics.py`) — solo cambia
  `CLOUDBANK_OBS_OTLP_ENDPOINT` de `http://jaeger:4317` a la URL de ingest de Grafana Cloud.
- Grafana Cloud Free incluye: Prometheus-compatible metrics (10k series), Loki logs (50 GB/mes),
  Tempo tracing (50 GB/mes) — suficiente para un proyecto académico.
- `structlog` sigue emitiendo JSON a stdout; Render captura stdout como logs nativos (retención
  7 días en el tier free, exportables a Grafana Loki vía OTLP para retención mayor).

---

## 8. Costo Mensual Estimado

| Servicio | Plan | Costo |
|---|---|---|
| Vercel (frontend) | Hobby | $0 |
| Render (backend) | Free | $0 |
| Render (ai-services) | Free | $0 |
| Neon (Postgres) | Free (0.5 GB, autosuspend) | $0 |
| Upstash Redis | Free (256 MB, 500K comandos/mes) | $0 |
| Upstash QStash | Free (500 mensajes/día) | $0 |
| Supabase Storage | Free (1 GB, 2 GB transferencia) | $0 |
| Grafana Cloud | Free (10k métricas, 50GB logs/traces) | $0 |
| GitHub Actions | Free (repo público) / 2000 min (privado) | $0 |
| GitHub (repo + Secrets) | Free | $0 |
| Clerk (opcional, auth UI) | Free (hasta 10k MAU) | $0 |
| LLM (Anthropic/OpenAI/Gemini) | Pay-as-you-go | **~$0.03–0.05 por evaluación** (único costo variable real) |
| **TOTAL infraestructura fija** | | **$0.00/mes** |

El único gasto real es el consumo de tokens LLM por solicitud evaluada (ver
`MASTER_ARCHITECTURE.md §10.1`, ~$0.04/solicitud con Claude Sonnet). Para un proyecto académico
con decenas o cientos de evaluaciones de prueba, el costo total esperado es de **unos pocos
dólares**, cubribles con los créditos gratuitos que Anthropic/OpenAI/Google otorgan a cuentas
nuevas o de estudiante.

---

## 9. Límites de los Planes Gratuitos (a vigilar)

| Servicio | Límite clave | Riesgo si se excede |
|---|---|---|
| Neon Free | 0.5 GB almacenamiento, 1 proyecto activo, autosuspend a los 5 min de inactividad | Cold start ~1-2s al reactivar; sin costo pero latencia perceptible |
| Upstash Redis Free | 256 MB, 500K comandos/mes, 10K comandos/día en algunos planes | Rate limiter y checkpoints dejan de escribir → fallback necesario (ver nota abajo) |
| Upstash QStash Free | 500 mensajes/día | Suficiente para colas de notificación async del proyecto académico |
| Render Free | 750 h/mes compartidas, sleep tras 15 min, 512 MB RAM | Cold start en demo; sin RAM suficiente para cargas ML pesadas (modelos scikit-learn deben ser ligeros) |
| Vercel Hobby | 100 GB bandwidth/mes, funciones serverless 10s timeout (Hobby) | Proxy routes no deben hacer streaming largo; usar backend directo para LLM streaming |
| Supabase Storage Free | 1 GB almacenamiento, 2 GB transferencia/mes | Solo para documentos pequeños (KYC), no video/backups |
| GitHub Actions | 2000 min/mes (privado) | CI ya optimizado (~10 min/run, ver `ci.yml`) → ~200 runs/mes posibles |
| Clerk Free | 10,000 MAU | Muy por encima de necesidad académica |

**Mitigación de límites:** el `rate_limiter.py` ya implementado debe degradar con gracia si Redis
no responde (fail-open documentado en `MASTER_ARCHITECTURE.md §5.2`); igual para checkpoints de
LangGraph — usar `MemorySaver` como fallback si Upstash no está disponible (ya soportado por
`orchestrator/graph/credit_evaluation_graph.py`, que ya distingue dev/prod saver).

---

## 10. Estrategia de Migración a Producción Empresarial (sin reescribir el sistema)

Gracias a Clean Architecture + Ports & Adapters, cada salto es un cambio de **adapter de
infraestructura y variable de entorno**, no de lógica de dominio:

| Componente Free Tier (Fase 1) | Componente Empresarial (Fase 2, ver `MASTER_ARCHITECTURE.md`) | Qué cambia en código |
|---|---|---|
| Neon Postgres | RDS Multi-AZ / Cloud SQL HA | Solo `CLOUDBANK_DATABASE_URL` (mismo `postgres_repositories.py` con asyncpg) |
| Upstash Redis | ElastiCache Redis Cluster | Solo `CLOUDBANK_REDIS_URL` (mismo cliente `redis-py`) |
| Render/Fly.io (1 contenedor) | Kubernetes + HPA (2-10 réplicas) | Reusar `docker/Dockerfile` (ya multi-stage) + aplicar `k8s/deployment.yaml` (ya existe) |
| GitHub Secrets / env vars | HashiCorp Vault | Reemplazar `os.environ` por `vault_client.py` (ya implementado, solo activar) |
| Grafana Cloud Free | Prometheus + Grafana + Jaeger self-hosted | Mismo código OTel; solo cambia el endpoint OTLP |
| Sin API Gateway | Kong API Gateway + WAF | Añadir Kong delante de FastAPI; middlewares actuales no cambian |
| QStash | RabbitMQ / Amazon MSK | Reemplazar adapter de `infrastructure/messaging/event_publisher.py` |
| Supabase Storage | S3 + CloudFront | Cambiar adapter de almacenamiento (interfaz ya debe abstraerse, ver nota) |
| Vercel Hobby | Vercel Pro / CloudFront + S3 estático | Sin cambios de código, solo plan |

**Orden recomendado de migración cuando el proyecto pase a producción real:**
1. Vault (secrets) — mayor impacto de seguridad, menor esfuerzo (ya implementado, solo activar).
2. RDS/Cloud SQL — mover datos reales con `pg_dump`/`pg_restore` desde Neon.
3. ElastiCache — sin downtime, solo actualizar `REDIS_URL` y recalentar cache.
4. Kubernetes — reusar manifiestos `k8s/` ya existentes, no requieren reescritura.
5. Kong + Istio mTLS — capa añadida, no reemplaza nada del código de aplicación.

---

## 11. Brechas a Implementar (no existen aún en el repo)

- [ ] Frontend Next.js completo (`apps/web` o carpeta `frontend/`) — no existe todavía.
- [ ] `LLMProviderPort` + adapters OpenAI/Gemini (§5) — hoy solo hay Anthropic vía `langchain-anthropic`.
- [ ] Adapter Neon/Upstash explícito en `docker-compose.yml` para dev local con free tier real (hoy apunta a Postgres/Redis locales, lo cual sigue siendo válido para desarrollo).
- [ ] Integración QStash para notificaciones asíncronas al solicitante (hoy `notification_service.notify()` es un stub síncrono según el flujo descrito en `MASTER_ARCHITECTURE.md §3`).
- [ ] Workflow de GitHub Actions con paso de deploy a Render/Vercel (hoy `ci.yml` cubre lint→test→build, falta el hook de deploy).
- [ ] Documentación de rotación de `encryption_key` y `jwt_secret` en ausencia de Vault activo.

---

## 12. Glosario de Servicios Free Tier

| Término | Definición |
|---|---|
| Neon | Postgres serverless con autosuspend y branching de bases de datos (como Git para datos) |
| Upstash | Redis y colas de mensajes (QStash) serverless, cobro por comando, con tier gratuito generoso |
| Render | PaaS que despliega contenedores Docker directamente desde un repo, con tier free con sleep |
| Fly.io | PaaS orientado a edge/global con VMs ligeras (Firecracker), sin sleep en el free allowance |
| Vercel | Plataforma de frontend optimizada para Next.js, CDN global incluido |
| QStash | Cola de mensajes HTTP-based de Upstash — reintentos y DLQ sin mantener un broker |
| Clerk | Servicio de autenticación gestionada (UI + backend) con tier gratuito hasta 10k usuarios activos |
