import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { lookupDni, BackendError } from "@/lib/backend-client";

export const runtime = "nodejs";

export async function GET(request: NextRequest, { params }: { params: { numero: string } }) {
  const token = cookies().get("cloudbank_session")?.value;
  if (!token) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  try {
    const identity = await lookupDni(token, params.numero);
    return NextResponse.json(identity);
  } catch (error) {
    if (error instanceof BackendError) {
      return NextResponse.json(error.detail ?? { error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Error al conectar con el backend" }, { status: 502 });
  }
}
