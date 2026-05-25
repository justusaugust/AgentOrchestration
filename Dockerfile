FROM python:3.12-slim AS builder

ARG AO_BUILD_CONFIG

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
RUN python -c "import subprocess, sys, tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *deps])"


FROM python:3.12-slim AS runtime

ARG AO_BUILD_VERSION=local

LABEL org.opencontainers.image.title="agent-orchestrator" \
      org.opencontainers.image.description="Agent Orchestration API runtime" \
      org.opencontainers.image.version="${AO_BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/orchestration-agent/AgentOrchestration"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY src ./src

EXPOSE 8000
CMD ["uvicorn", "src.api.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
