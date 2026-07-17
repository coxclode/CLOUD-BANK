# CLOUD BANK — Infraestructura de Despliegue Real (Producción)

Este diagrama documenta el despliegue efectivamente realizado (a diferencia de
`FREE_TIER_ARCHITECTURE.md`, que es el plan de referencia). Stack: Vercel +
Render + Neon, con las imágenes de `backend`/`ai-services` publicadas en
Docker Hub.

## Diagrama de infraestructura

```mermaid
flowchart TB
    subgraph Internet["Internet"]
        User["Usuario / Oficial de riesgo"]
    end

    subgraph Vercel["Vercel (Hobby, Free)"]
        Frontend["frontend — Next.js 14\nApp Router + Route Handlers\n/login · /apply · /status"]
    end

    subgraph DockerHub["Docker Hub"]
        ImgBackend["luismendozaa/cloudbank-backend:latest"]
        ImgAI["luismendozaa/cloudbank-ai-services:latest"]
    end

    subgraph Render["Render (Oregon, Free)"]
        Backend["backend — FastAPI\nClean Architecture\nJWT + RBAC + rate limiting"]
        AIServices["ai-services — LangGraph\n4 Deep Agents\n(fraude, crédito, actuarial, aprobación)"]
        Redis["Redis (Key Value)\nrate limiting, cache"]
    end

    subgraph Neon["Neon (Serverless Postgres, Free)"]
        Postgres[("PostgreSQL\ncredit_applications\ncredit_decisions")]
    end

    subgraph External["Servicios externos"]
        Anthropic["Anthropic API\n(Claude Sonnet / Haiku)"]
        Decolecta["Decolecta API\n(RENIEC — validación de DNI)"]
    end

    User -->|HTTPS| Frontend
    Frontend -->|"Route Handlers\n(oculta BACKEND_URL)"| Backend
    Backend -->|"HTTP interno\nCLOUDBANK_AI_SERVICES_URL"| AIServices
    Backend -->|"asyncpg\nsslmode=require"| Postgres
    Backend -->|"redis:// (TLS)"| Redis
    AIServices -->|"HTTPS"| Anthropic
    Backend -->|"HTTPS\nBearer token"| Decolecta

    ImgBackend -.->|"docker pull\n(deploy)"| Backend
    ImgAI -.->|"docker pull\n(deploy)"| AIServices

    style Frontend fill:#1a1a2e,color:#fff,stroke:#4361ee
    style Backend fill:#1a1a2e,color:#fff,stroke:#4361ee
    style AIServices fill:#1a1a2e,color:#fff,stroke:#4361ee
    style Redis fill:#7a1f1f,color:#fff,stroke:#e63946
    style Postgres fill:#1f4d2f,color:#fff,stroke:#2a9d8f
    style Anthropic fill:#3d2b56,color:#fff,stroke:#9b5de5
    style Decolecta fill:#3d2b56,color:#fff,stroke:#9b5de5
```

## Flujo de despliegue (CI manual — sin GitHub Actions todavía)

```mermaid
sequenceDiagram
    participant Dev as Máquina local
    participant DH as Docker Hub
    participant Render as Render
    participant Neon as Neon
    participant Vercel as Vercel

    Dev->>Dev: docker compose build (backend, ai-services)
    Dev->>DH: docker push cloudbank-backend / cloudbank-ai-services
    Dev->>Neon: Aplicar schema.sql (tablas credit_applications / credit_decisions)
    Dev->>Render: render services create --image docker.io/.../cloudbank-ai-services
    Dev->>Render: render services create --image docker.io/.../cloudbank-backend
    Dev->>Render: render keyvalues create (Redis, plan free)
    Note over Render: Backend conecta a Neon (Postgres) y Redis (Render KV)
    Dev->>Vercel: vercel env add BACKEND_URL production
    Dev->>Vercel: vercel deploy --prod
    Note over Vercel: Frontend apunta a https://cloudbank-backend.onrender.com
```

## Servicios y su función

| Servicio | Plataforma | Plan | Función |
|---|---|---|---|
| `frontend` | Vercel | Hobby (Free) | UI Next.js, Route Handlers como proxy al backend |
| `backend` | Render (Web Service) | Free | API REST, autenticación JWT, persistencia |
| `ai-services` | Render (Web Service) | Free | Pipeline LangGraph de 4 Deep Agents sobre Claude |
| Redis | Render (Key Value) | Free | Rate limiting, cache de sesión |
| PostgreSQL | Neon | Free (0.5 GB) | Persistencia de solicitudes y decisiones de crédito |
| Registro de imágenes | Docker Hub | Free | Origen de las imágenes que Render despliega |
| LLM | Anthropic API | Pay-as-you-go | Razonamiento de los 4 agentes de IA |
| Verificación de identidad | Decolecta API | Free tier | Consulta RENIEC por DNI (Perú) |

## Limitaciones conocidas de este despliegue

- **`ai-services` es públicamente accesible** sin autenticación propia (limitación
  del plan free de Render, que no permite servicios privados). Ver mejora
  propuesta: header `X-Internal-Key` compartido entre `backend` y `ai-services`.
- **Cold start**: los servicios de Render (plan free) "duermen" tras ~15 min de
  inactividad; el primer request posterior puede tardar 30–50s.
- **Sin CI/CD**: el despliegue es manual (build → push → `render services
  create/update` → `vercel deploy`). No hay pipeline de GitHub Actions que
  automatice este flujo todavía.
- **Observabilidad reducida en producción**: Jaeger/Prometheus/Grafana solo
  corren en el `docker-compose.yml` local; en Render/Vercel no hay stack de
  observabilidad conectado (`CLOUDBANK_OBS_TRACING_ENABLED=false`).
