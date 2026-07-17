import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { evaluateCreditApplication, BackendError } from "@/lib/backend-client";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const token = cookies().get("cloudbank_session")?.value;
  if (!token) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const payload = await request.json();

  try {
    const decision = await evaluateCreditApplication(token, payload);
    return NextResponse.json(decision);
  } catch (error) {
    if (error instanceof BackendError) {
      return NextResponse.json(error.detail ?? { error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Error al conectar con el backend" }, { status: 502 });
  }
}
