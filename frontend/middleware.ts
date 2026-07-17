import { NextRequest, NextResponse } from "next/server";

const PROTECTED_PREFIXES = ["/apply", "/status"];

export function middleware(request: NextRequest) {
  const isProtected = PROTECTED_PREFIXES.some((prefix) => request.nextUrl.pathname.startsWith(prefix));
  if (!isProtected) {
    return NextResponse.next();
  }

  const hasSession = request.cookies.has("cloudbank_session");
  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/apply/:path*", "/status/:path*"],
};
