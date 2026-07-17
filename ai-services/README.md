# ai-services

Motor de IA de CLOUD BANK: LangGraph + 4 Deep Agents (fraude, historial crediticio,
actuarial, aprobación) + capa de abstracción multi-proveedor LLM (Anthropic/OpenAI/Gemini).

Servicio interno — expone una única API REST consumida solo por `backend/`.
Nunca toca la base de datos ni es alcanzable desde el frontend.

## Ejecutar de forma independiente

```bash
cp .env.example .env    # completar ANTHROPIC_API_KEY (u OPENAI_/GEMINI_)
pip install -e ".[dev]"
uvicorn src.api.main:app --port 8100 --reload
```

## Endpoints

| Método | Ruta                     | Descripción                              |
|--------|--------------------------|-------------------------------------------|
| POST   | `/v1/pipeline/evaluate`  | Ejecuta el pipeline de 4 agentes          |
| GET    | `/health`                | Liveness                                  |

Ver [`docs/SERVICE_CONTRACTS.md`](../docs/SERVICE_CONTRACTS.md) para el contrato completo.

## Tests

```bash
pytest tests/ -v
```
