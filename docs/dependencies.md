# Dependencies

## Direct runtime dependencies

| Package | Constraint | Why |
| --- | --- | --- |
| `mcp` | `>=2.3,<3` | Official MCP Python SDK: client-facing Streamable HTTP server, remote upstream client, protocol types (`mcp-types`) |
| `fastapi` | `>=0.120,<1` | HTTP application, admin API, OpenAPI schema |
| `uvicorn` | `>=0.30,<1` | ASGI server |
| `pydantic` | `>=2.11,<3` | Validated configuration and protocol models |
| `pydantic-settings` | `>=2.10,<3` | Environment-variable overrides |
| `pyyaml` | `>=6.0,<7` | YAML configuration (`safe_load` only) |
| `prometheus-client` | `>=0.21,<1` | Metrics exposition |
| `opentelemetry-api` | `>=1.27,<2` | Tracing spans (no-op unless an SDK is configured) |
| `pip` | `>=26.2` | Installs hash-locked package dependencies (`pip --python <env>`) |

Lower bounds are versions the test suite has been run against. Upper bounds
guard against untested major releases; Dependabot proposes updates weekly
and CI runs `pip-audit` on every PR and weekly.

## Optional extras

| Extra | Package | Why |
| --- | --- | --- |
| `s3` | `boto3>=1.35,<2` (Apache-2.0) | S3-compatible package artifact storage |
| `mongodb` | `pymongo>=4.13,<5` (Apache-2.0) | MongoDB package catalog |

Both are installed in the Docker image. `moto[server]` (Apache-2.0) is a
development-only dependency that provides an in-process S3-compatible server
for tests.

## Full runtime inventory

Every package installed by `pip install .` into a clean Python 3.11
environment on 2026-10-11, with the license declared in its metadata. All
are permissive licenses compatible with Apache-2.0.

| Package | Version | License |
| --- | --- | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.143.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| mcp | 2.3.0 | MIT |
| mcp-types | 2.3.0 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| pip | 26.2.1 | MIT |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.0 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pydantic_core | 2.50.0 | MIT |
| pyjwt | 2.15.1 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pyyaml | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |

Regenerate before each release (see [release-process.md](release-process.md))
and keep the Apache-2.0 `NOTICE` file accurate.

## Development dependencies

`pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`, `types-PyYAML`, `boto3`, `pymongo`, `moto[server]`
(`pip install -e ".[dev]"`). They are not shipped in the package or image.
