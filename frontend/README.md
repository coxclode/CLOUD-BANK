# frontend

Next.js 14 (App Router) + TypeScript + Tailwind. Scaffold funcional mínimo:
login de oficiales, formulario de solicitud de crédito y consulta de estado.

**Regla de arquitectura:** este proyecto nunca importa un driver de base de
datos ni llama a un LLM. Solo conoce una cosa: la API REST del backend
(`BACKEND_URL`, variable server-only en `lib/backend-client.ts`). Todo el
tráfico saliente pasa por Route Handlers (`app/api/**`) para que la URL y las
credenciales del backend nunca lleguen al navegador.

## Ejecutar de forma independiente

```bash
cp .env.local.example .env.local   # apuntar BACKEND_URL al backend corriendo
npm install
npm run dev
```

## Flujo

```
Usuario → /login → POST /api/auth/login → backend POST /v1/auth/login → cookie httpOnly
Usuario → /apply → POST /api/proxy/evaluate → backend POST /v1/credit/evaluate
Usuario → /status/[id] → backend GET /v1/credit/{id}
```
