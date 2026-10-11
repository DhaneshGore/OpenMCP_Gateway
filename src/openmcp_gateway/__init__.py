"""OpenMCP Gateway — a provider-neutral gateway for Model Context Protocol servers."""

__version__ = "0.1.0.dev0"

# The product name is deliberately a single constant (and overridable via
# `gateway.name` in configuration) because the project name is provisional.
PROJECT_NAME = "OpenMCP Gateway"
PROJECT_SLUG = "openmcp-gateway"

__all__ = ["PROJECT_NAME", "PROJECT_SLUG", "__version__"]
