# syntax=docker/dockerfile:1.7
# Imagem única para os três serviços (wa-ingress, lead-agent, leads-dashboard).
# O serviço é escolhido pela variável WLA_APP no Cloud Run.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.lock ./
# --require-hashes: só instala pacotes cujo hash bate com o lock (cadeia de suprimentos).
RUN pip install --require-hashes --only-binary=:all: -r requirements.lock

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src WLA_APP=ingress PORT=8080
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# Código somente leitura para o usuário da aplicação (pertence ao root).
COPY src ./src
USER 10001:10001
EXPOSE 8080
HEALTHCHECK NONE
# exec: o uvicorn vira PID 1 e recebe o SIGTERM do Cloud Run (desligamento gracioso).
CMD ["sh", "-c", "exec uvicorn --factory \"wla.${WLA_APP}.app:create_app\" --host 0.0.0.0 --port ${PORT} --no-server-header --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 8"]
