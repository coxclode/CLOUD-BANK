import "server-only";

/**
 * Único punto de contacto con el backend. Corre exclusivamente en el servidor
 * de Next.js (Route Handlers / Server Components) — BACKEND_URL y las
 * credenciales nunca se envían al navegador. El frontend nunca habla con
 * ai-services ni con la base de datos: solo con esta API REST del backend.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export class BackendError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown
  ) {
    super(message);
  }
}

async function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
    cache: "no-store",
  });
}

export async function login(username: string, password: string) {
  const response = await backendFetch("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new BackendError("Login fallido", response.status, detail);
  }
  return (await response.json()) as { access_token: string; expires_in_minutes: number };
}

export async function evaluateCreditApplication(token: string, payload: unknown) {
  const response = await backendFetch("/v1/credit/evaluate", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new BackendError("La evaluación de crédito falló", response.status, body);
  }
  return body;
}

export async function lookupDni(token: string, dni: string) {
  const response = await backendFetch(`/v1/identity/dni/${dni}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new BackendError("No se pudo validar el DNI", response.status, body);
  }
  return body as {
    document_number: string;
    first_name: string;
    first_last_name: string;
    second_last_name: string;
    full_name: string;
  };
}

export async function getApplicationStatus(token: string, applicationId: string) {
  const response = await backendFetch(`/v1/credit/${applicationId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new BackendError("No se pudo obtener el estado de la solicitud", response.status, body);
  }
  return body;
}
