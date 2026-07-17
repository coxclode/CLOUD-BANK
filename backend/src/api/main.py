"""
Backend — Punto de entrada ASGI (uvicorn src.api.main:app).
"""

from __future__ import annotations

from src.api.app import create_app

app = create_app()


def run() -> None:
    import uvicorn
    from config.settings import get_settings

    settings = get_settings()
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        workers=1 if settings.app.environment != "production" else 4,
        log_level=settings.app.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    run()
