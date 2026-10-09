"""Utilidades HTTP compartilhadas: cabeçalhos de segurança e leitura de corpo com limite."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

API_CSP = "default-src 'none'; frame-ancestors 'none'"


def install_security_headers(app: FastAPI, csp: str = API_CSP) -> None:
    """Adiciona cabeçalhos defensivos (OWASP Secure Headers) a todas as respostas."""

    @app.middleware("http")
    async def _headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Content-Security-Policy", csp)
        return response


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Lê o corpo em streaming e aborta com 413 ao passar do limite (evita DoS por payload)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise HTTPException(status_code=413, detail="payload grande demais")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="content-length inválido") from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail="payload grande demais")
        chunks.append(chunk)
    return b"".join(chunks)
