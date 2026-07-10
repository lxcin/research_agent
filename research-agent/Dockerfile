FROM python:3.11-slim

LABEL org.opencontainers.image.description="Research Agent - persistent research partner with RAG and memory"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY src/ src/
COPY tests/ tests/

ENV RESEARCH_AGENT_DATA_DIR=/data
ENV RESEARCH_AGENT_LLM_KEY=""

RUN mkdir -p /data

VOLUME ["/data"]

ENTRYPOINT ["research-agent"]
CMD ["chat"]