import { NextRequest, NextResponse } from "next/server";
import { login, BackendError } from "@/lib/backend-client";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const { username, password } = await request.json();

  try {
    const { access_token, expires_in_minutes } = await login(username, password);

    const response = NextResponse.json({ ok: true });
    response.cookies.set("cloudbank_session", access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: expires_in_minutes * 60,
    });
    return response;
  } catch (error) {
    if (error instanceof BackendError) {
      return NextResponse.json({ error: "Credenciales inválidas" }, { status: error.status });
    }
    return NextResponse.json({ error: "Error al conectar con el backend" }, { status: 502 });
  }
}
