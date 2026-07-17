# CLOUD BANK — Arquitectura del Sistema Multiagente de Evaluación Crediticia

## Versión 2.0 | Clasificación: CONFIDENCIAL — USO INTERNO

---

## 1. DIAGRAMA LÓGICO DEL SISTEMA

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                          CLOUD BANK — CREDIT ENGINE v2.0                        ║
║                     Sistema Multiagente de Evaluación Crediticia                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CAPA DE ENTRADA                                     │
│                                                                                   │
│   Cliente ──→  [ Kong API Gateway ]  ──→  [ TLS 1.3 ]  ──→  [ FastAPI ]        │
│                      │                                           │                │
│               Rate Limiting                              Payload Validation       │
│               Auth (API Key + JWT)                       Security Headers         │
│               WAF                                        CORS                     │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         CAPA DE SEGURIDAD (Zero Trust)                           │
│                                                                                   │
│   ┌─────────────────┐    ┌──────────────────┐    ┌──────────────────────────┐   │
│   │  InputValidator  │    │   PromptGuard    │    │    SecurityContext        │   │
│   │                  │    │                  │    │                          │   │
│   │ • Esquemas Pydantic  │ • Anti-Injection  │    │ • Zero Trust Check       │   │
│   │ • Sanitización PII   │ • Anti-Jailbreak  │    │ • IP / Device Intel      │   │
│   │ • Validación tipos   │ • Anti-Poisoning  │    │ • Autenticación          │   │
│   │ • Consent check  │   │ • Homoglyph detect    │ • Threat Assessment      │   │
│   └─────────────────┘    └──────────────────┘    └──────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    MOTOR LANGGRAPH — GRAFO DE EVALUACIÓN                         │
│                                                                                   │
│   START                                                                           │
│     │                                                                             │
│     ▼                                                                             │
│   ┌─────────────────┐                                                             │
│   │ validate_input  │──(error)──────────────────────────────────→ [handle_error] │
│   └────────┬────────┘                                                             │
│            │ (ok)                                                                 │
│            ▼                                                                      │
│   ┌─────────────────┐                                                             │
│   │ security_check  │──(error)──────────────────────────────────→ [handle_error] │
│   └────────┬────────┘                                                             │
│            │ (ok)                                                                 │
│            ▼                                                                      │
│   ┌─────────────────────────────────────────────────────────┐                   │
│   │              AGENTE ANTIFRAUDE                           │                   │
│   │                                                          │                   │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │                   │
│   │  │ Document │  │Biometric │  │Behavioral│  │Device  │  │                   │
│   │  │ Verify   │  │Analysis  │  │Signals   │  │Intel   │  │ (paralelo)        │
│   │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │                   │
│   │                                             ┌────────┐  │                   │
│   │                                             │IP Repú-│  │                   │
│   │                                             │tation  │  │                   │
│   │                                             └────────┘  │                   │
│   │                    ┌──────────────────────────────┐      │                   │
│   │                    │  LLM Synthesis (Claude)       │      │                   │
│   │                    │  fraud_score + explanation    │      │                   │
│   │                    └──────────────────────────────┘      │                   │
│   └──────────────────────────┬──────────────────────────────┘                   │
│                               │                                                   │
│                    (critical fraud) ──────────────────────→ [handle_error]       │
│                               │ (pass / flag)                                     │
│                               ▼                                                   │
│   ┌─────────────────────────────────────────────────────────┐                   │
│   │           AGENTE HISTORIAL CREDITICIO                    │                   │
│   │                                                          │                   │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │                   │
│   │  │ Bureau   │  │Income    │  │Expense   │  │AML     │  │ (paralelo)        │
│   │  │ Query    │  │Verify    │  │Analyze   │  │Screen  │  │                   │
│   │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │                   │
│   │                    ┌──────────────────────────────┐      │                   │
│   │                    │  LLM Synthesis (Claude)       │      │                   │
│   │                    │  credit_risk + explanation    │      │                   │
│   │                    └──────────────────────────────┘      │                   │
│   └──────────────────────────┬──────────────────────────────┘                   │
│                               │                                                   │
│                    (AML hit) ─────────────────────────────→ [handle_error]       │
│                               │ (ok)                                              │
│                               ▼                                                   │
│   ┌─────────────────────────────────────────────────────────┐                   │
│   │                 AGENTE ACTUARIO                          │                   │
│   │                                                          │                   │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │                   │
│   │  │Logistic  │  │Gradient  │  │Neural    │  (paralelo)  │                   │
│   │  │Regression│  │Boosting  │  │Network   │              │                   │
│   │  └────┬─────┘  └────┬─────┘  └────┬─────┘              │                   │
│   │       └─────────────┴─────────────┘                     │                   │
│   │                      │                                   │                   │
│   │              ┌───────┴────────┐                          │                   │
│   │              │    Ensemble    │  + SHAP + Loss Metrics   │                   │
│   │              └───────┬────────┘                          │                   │
│   │                      │                                   │                   │
│   │                    ┌──────────────────────────────┐      │                   │
│   │                    │  LLM Synthesis (Claude)       │      │                   │
│   │                    │  actuarial_score + explain    │      │                   │
│   │                    └──────────────────────────────┘      │                   │
│   └──────────────────────────┬──────────────────────────────┘                   │
│                               │ (ok)                                              │
│                               ▼                                                   │
│   ┌─────────────────────────────────────────────────────────┐                   │
│   │               AGENTE APROBADOR                          │                   │
│   │                                                          │                   │
│   │   [Hard Rules Engine]  →  [LLM Decision Synthesis]      │                   │
│   │                                                          │                   │
│   │    APPROVED ──→ [credit_terms generados]                │                   │
│   │    REJECTED ──→ [rejection_reasons documentados]        │                   │
│   │    MORE_DOCS ─→ [required_documents listados]           │                   │
│   │    ESCALATED ─→ [escalation_package preparado]          │                   │
│   └──────────┬───────────────────────┬──────────────────────┘                   │
│              │                       │                                            │
│     (APPROVED/REJECTED/            (ESCALATED)                                   │
│      MORE_DOCS)                       │                                           │
│              │                       ▼                                            │
│              │            ┌─────────────────────┐                                │
│              │            │  human_escalation    │                                │
│              │            │  (Cola de Comité)    │                                │
│              │            └──────────┬───────────┘                                │
│              │                       │                                            │
│              └───────────┬───────────┘                                            │
│                          ▼                                                        │
│                ┌─────────────────────┐                                            │
│                │   audit_finalize    │                                            │
│                │  (Cierre expediente)│                                            │
│                └──────────┬──────────┘                                            │
│                           │                                                       │
│                          END                                                      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. ESTADOS DEL GRAFO LANGGRAPH

| Estado del Estado | Descripción | Nodo Origen |
|---|---|---|
| `PENDING` | Solicitud recibida, no procesada | START |
| `IN_REVIEW` | Procesándose por el pipeline | validate_input → approval |
| `APPROVED` | Crédito aprobado con condiciones | approval_decision |
| `REJECTED` | Crédito rechazado con razones | approval_decision / handle_error |
| `MORE_DOCS_REQUIRED` | Faltan documentos específicos | approval_decision |
| `ESCALATED_TO_COMMITTEE` | Revisión humana necesaria | human_escalation |
| `BLOCKED_FRAUD` | Fraude crítico detectado | handle_error |
| `ERROR` | Error técnico irrecuperable | handle_error |

---

## 3. NODOS Y SUS RESPONSABILIDADES

```
┌──────────────────────┬──────────────────────────────────────────────────────────┐
│ NODO                 │ RESPONSABILIDAD                                           │
├──────────────────────┼──────────────────────────────────────────────────────────┤
│ validate_input       │ Valida esquema Pydantic, sanitiza PII, calcula hash       │
│ security_check       │ Zero Trust: IP, dispositivo, autenticación, flags         │
│ fraud_detection      │ Antifraude: 5 verificaciones paralelas + LLM              │
│ credit_history       │ Bureau + AML + ingresos + gastos + capacidad pago         │
│ actuarial_analysis   │ 3 modelos ML + ensemble + SHAP + pérdida esperada         │
│ approval_decision    │ Hard rules + LLM synthesis → decisión vinculante          │
│ human_escalation     │ Encola en sistema de comité humano                        │
│ audit_finalize       │ Cierra expediente, calcula métricas totales               │
│ handle_error         │ Recovery: registra error, devuelve estado estructurado    │
└──────────────────────┴──────────────────────────────────────────────────────────┘
```

---

## 4. HERRAMIENTAS POR AGENTE

### Agente Antifraude
| Herramienta | Servicio | Timeout | Fallback |
|---|---|---|---|
| `verify_document` | Document Intelligence API | 10s | Evalúa por presencia de docs |
| `analyze_biometrics` | Biometric Service | 10s | Score 0.5 neutro |
| `analyze_behavioral_signals` | Session Analytics | 10s | Score 0.1 bajo riesgo |
| `check_device_intelligence` | Device Intel API | 10s | Score 0.6 neutro |
| `check_ip_reputation` | IP Reputation API | 10s | Score 0.7 del contexto |

### Agente Historial Crediticio
| Herramienta | Servicio | Timeout | Fallback |
|---|---|---|---|
| `query_credit_bureau` | Multi-Bureau API | 12s | CreditBureauData vacío |
| `run_aml_check` | AML Screening | 12s | `clear=True, FLAG` |
| `verify_income_sources` | Tax Records API | 12s | 85% del ingreso declarado |
| `analyze_expense_pattern` | Bureau Expenses | 12s | Usa declarado |

### Agente Actuario
| Herramienta | Función | Tipo |
|---|---|---|
| `run_logistic_regression_model` | PD regulatoriamente interpretable | Estadístico |
| `run_gradient_boosting_model` | PD alta precisión | ML |
| `run_neural_network_model` | PD patrones complejos | Deep Learning |
| `compute_ensemble_score` | PD combinada (LR×0.30 + GBM×0.45 + NN×0.25) | Ensemble |
| `compute_shap_values` | Importancia de variables | Explicabilidad |
| `estimate_loss_metrics` | EL, UL, RWA (Basel III) | Actuarial |

### Agente Aprobador
| Componente | Función |
|---|---|
| Hard Rules Engine | Reglas de negocio no negociables (pre-LLM) |
| LLM Synthesis | Decisión holística + justificación |
| CreditTerms Builder | Calcula condiciones de crédito aprobado |
| EscalationPackage Builder | Prepara expediente para comité humano |

---

## 5. INPUTS Y OUTPUTS POR AGENTE

### Inputs del Sistema
```json
{
  "identity": {
    "national_id": "string",
    "id_type": "string",
    "full_name": "string",
    "date_of_birth": "YYYY-MM-DD",
    "nationality": "string",
    "tax_id": "string|null"
  },
  "contact": {
    "email": "string",
    "phone": "string",
    "address": "string",
    "city": "string",
    "country": "string",
    "postal_code": "string"
  },
  "credit_request": {
    "requested_amount": "float",
    "term_months": "int [6-84]",
    "purpose": "string",
    "currency": "USD"
  },
  "monthly_income": "float",
  "employment_type": "string",
  "employment_months": "int",
  "consent_given": true
}
```

### Output Final del Sistema
```json
{
  "request_id": "uuid",
  "correlation_id": "uuid",
  "status": "APPROVED|REJECTED|MORE_DOCS_REQUIRED|ESCALATED_TO_COMMITTEE|ERROR",
  "decision_summary": {
    "decision": "string",
    "confidence": "float [0-1]",
    "justification": "string",
    "credit_terms": {
      "approved_amount": "float",
      "interest_rate_annual": "float",
      "term_months": "int",
      "monthly_installment": "float",
      "total_cost": "float"
    },
    "rejection_reasons": ["string"],
    "required_documents": ["string"],
    "escalated": "bool",
    "total_duration_ms": "float"
  },
  "fraud_risk": "MINIMAL|LOW|MEDIUM|HIGH|CRITICAL",
  "credit_risk": "MINIMAL|LOW|MEDIUM|HIGH|CRITICAL",
  "actuarial_score": "float [0-1000]",
  "errors": []
}
```

---

## 6. MEMORIA DE LOS AGENTES

| Tipo | Implementación | Scope | TTL |
|---|---|---|---|
| **Estado compartido** | `CreditEvaluationState` (Pydantic) | Request completo | Duración del request |
| **Checkpointing** | `MemorySaver` (dev) / `AsyncRedisSaver` (prod) | Thread (request_id) | Configurable |
| **Contexto de mensaje** | `add_messages` (LangGraph) | Por agente | Duración del agente |
| **Cache de resultados** | Redis (producción) | Cross-request | 1 hora |
| **Memoria episódica** | Audit trail en estado | Inmutable | Permanente (audit DB) |

---

## 7. GESTIÓN DE ERRORES Y RECUPERACIÓN

```
Error Level          Acción                     Reintentable
─────────────────    ──────────────────────     ────────────
Timeout de agente    Reintentar (max 3x)        Sí
API externa caída    Fallback degradado          Sí
LLM parse error      Respuesta por defecto       Sí
Fraude crítico       Bloqueo inmediato           No
AML positivo         Bloqueo regulatorio         No
Validación input     Error 400                  No
Auth failure         Error 401                  No
Estado corrupto      Escalación a humano         No
Max retries exceeded Error + Escalación          No
```

### Estrategia de Reintentos (Tenacity)
```
Agente → Fallo → Reintento 1 (2s) → Reintento 2 (4s) → Reintento 3 (8s) → Fallback
                                                                        ↓
                                                           Estado degradado o ERROR
```

---

## 8. SEGURIDAD — DEFENSA EN CAPAS

```
Capa 1: Red          TLS 1.3, mTLS entre servicios (Istio), NetworkPolicy
Capa 2: Perímetro    WAF, Rate Limiting, DDoS protection (Kong)
Capa 3: API          Auth API Key + JWT, CORS, Security Headers
Capa 4: Input        Pydantic strict, tamaño payload, sanitización HTML
Capa 5: Prompt       PromptGuard: injection, jailbreak, poisoning, homoglyphs
Capa 6: Datos        Encriptación AES-256 (Fernet), tokenización PII
Capa 7: Container    Non-root, read-only FS, seccomp, no capabilities
Capa 8: K8s          RBAC mínimo, secrets desde Vault, Pod Security Standards
Capa 9: Auditoría    Audit trail inmutable, logs estructurados JSON
```

---

## 9. OBSERVABILIDAD

| Componente | Herramienta | Qué mide |
|---|---|---|
| **Tracing** | OpenTelemetry + Jaeger | Latencia por nodo, dependencias |
| **Métricas** | Prometheus + Grafana | Throughput, errores, scores |
| **Logs** | structlog (JSON) + ELK | Eventos de negocio, auditoría |
| **Alertas** | AlertManager | PD > 0.8, fraudes críticos, latencia |

### Métricas Clave (KPIs)
```
cloudbank_applications_total          → Volumen de solicitudes
cloudbank_agent_duration_seconds      → Latencia por agente (P50, P95, P99)
cloudbank_fraud_score_distribution    → Distribución de riesgo de fraude
cloudbank_default_probability_*       → Distribución de PD del portafolio
cloudbank_approvals_total             → Tasa de aprobación/rechazo
cloudbank_escalations_total           → Tasa de escalaciones
cloudbank_llm_tokens_used             → Costo de LLM por solicitud
```

---

## 10. ESCALABILIDAD Y RENDIMIENTO

### Latencia objetivo por nodo
```
validate_input:      < 50ms
security_check:      < 30ms
fraud_detection:     < 8s  (5 paralelos + LLM synthesis)
credit_history:      < 6s  (4 paralelos + LLM synthesis)
actuarial_analysis:  < 6s  (3 modelos paralelos + LLM)
approval_decision:   < 4s  (LLM synthesis)
─────────────────────────────
Total P95:           < 25s
Total P99:           < 35s
```

### Escalabilidad Horizontal
```
Componente          Escala           Estrategia
──────────────────  ──────────────   ─────────────────────
FastAPI workers     Vertical         Multiple workers Uvicorn
K8s Pods           HPA 3→10         CPU > 70% o Memory > 80%
Redis              Cluster mode     Redis Sentinel / Cluster
LLM calls          No state         Múltiples instancias concurrentes
Servicios externos Via Gateway      Load balancing + circuit breaker
```

### Estimación de Costos LLM por solicitud
```
Agente              Modelo              Input tokens  Output tokens  Costo USD*
─────────────────── ─────────────────── ───────────   ────────────   ──────────
fraud_detection     claude-sonnet-4-6   ~2,000        ~400           ~$0.010
credit_history      claude-sonnet-4-6   ~1,800        ~400           ~$0.009
actuarial_analysis  claude-sonnet-4-6   ~1,500        ~500           ~$0.008
approval_decision   claude-sonnet-4-6   ~2,500        ~600           ~$0.013
─────────────────────────────────────────────────────────────────────────────
TOTAL por solicitud                                                  ~$0.040

*Precios referenciales Claude claude-sonnet-4-6 (verificar pricing actual en anthropic.com)
```

---

## 11. DIAGRAMA DE DESPLIEGUE (Kubernetes)

```
Internet
   │
   ▼
[ Ingress / Load Balancer ]
   │
   ▼
[ Kong API Gateway ] ← WAF, Rate Limit, Auth
   │
   ├─→ [credit-engine Pod 1] ─┐
   ├─→ [credit-engine Pod 2] ─┼── [ Redis Cluster ] ← Checkpointing + Cache
   └─→ [credit-engine Pod 3] ─┘         │
           │                              └─→ [ Audit DB (PostgreSQL) ]
           │
           ├─→ [ Biometric Service ]
           ├─→ [ Bureau Service ]
           ├─→ [ Device Intel Service ]
           ├─→ [ IP Reputation Service ]
           ├─→ [ AML Service ]
           └─→ [ Anthropic API ] ← LLM (cloud)

Observabilidad:
   credit-engine → [ Jaeger Collector ] → [ Jaeger UI ]
   credit-engine → [ Prometheus ]       → [ Grafana ]
   credit-engine → [ Fluentd ]          → [ Elasticsearch ] → [ Kibana ]
```

---

## 12. FLUJO DE DATOS (GDPR / PCI-DSS)

```
1. Cliente envía datos por TLS 1.3 (en tránsito encriptados)
2. API desencripta en memoria — NUNCA escribe datos en claro a disco
3. InputValidator sanitiza y aplica máscaras de PII para logs
4. PromptGuard verifica antes de enviar al LLM
5. Datos PII se hashean (SHA-256) antes de consultas a servicios externos
6. LLM solo recibe datos sanitizados sin PII directa
7. Estado en Redis encriptado (AES-256 Fernet)
8. Audit trail en PostgreSQL encriptado at rest
9. Al finalizar: datos volátiles eliminados de memoria
10. Solo persiste: request_id, decision, audit trail (sin PII raw)
```
