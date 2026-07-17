import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { getApplicationStatus, BackendError } from "@/lib/backend-client";

export const runtime = "nodejs";

export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  const token = cookies().get("cloudbank_session")?.value;
  if (!token) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  try {
    const status = await getApplicationStatus(token, params.id);
    return NextResponse.json(status);
  } catch (error) {
    if (error instanceof BackendError) {
      return NextResponse.json(error.detail ?? { error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Error al conectar con el backend" }, { status: 502 });
  }
}
