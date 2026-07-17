"""
Middleware de cabeceras de seguridad HTTP.

Implementa las cabeceras recomendadas por OWASP:
  HSTS, CSP, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy.

Eliminación de cabeceras que revelan información del servidor.
"""

from __future__ import annotations

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):

    _HEADERS = {
        "Strict-Transport-Security":    "max-age=63072000; includeSubDomains; preload",
        "X-Content-Type-Options":       "nosniff",
        "X-Frame-Options":              "DENY",
        "X-XSS-Protection":            "1; mode=block",
        "Referrer-Policy":              "strict-origin-when-cross-origin",
        "Permissions-Policy":          (
            "geolocation=(), microphone=(), camera=(), "
            "payment=(), usb=(), interest-cohort=()"
        ),
        "Content-Security-Policy":      (
            "default-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'none'"
        ),
        "Cache-Control":               "no-store, no-cache, must-revalidate, private",
        "Pragma":                       "no-cache",
    }

    _REMOVE_HEADERS = {"Server", "X-Powered-By"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        for k, v in self._HEADERS.items():
            response.headers[k] = v

        for header in self._REMOVE_HEADERS:
            if header in response.headers:
                del response.headers[header]

        return response
