# syntax=docker/dockerfile:1
# OpenMCP Gateway container image.
#   docker build -t openmcp-gateway .
#   docker run --rm -p 127.0.0.1:8080:8080 -v ./examples/docker:/etc/openmcp:ro openmcp-gateway

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir ".[s3,mongodb]"

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="OpenMCP Gateway" \
      org.opencontainers.image.description="Provider-neutral gateway for Model Context Protocol servers" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/DhaneshGore/OpenMCP_Gateway.git"
RUN groupadd --system --gid 10001 openmcp \
 && useradd --system --uid 10001 --gid openmcp --home-dir /var/lib/openmcp --create-home openmcp
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    OPENMCP_CONFIG=/etc/openmcp/openmcp.yaml
WORKDIR /var/lib/openmcp
# Writable state (SQLite registry). Declared as a volume so the image also
# works with a read-only root filesystem when no volume is mounted explicitly.
VOLUME ["/var/lib/openmcp"]
USER openmcp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"
ENTRYPOINT ["openmcp"]
CMD ["serve"]
