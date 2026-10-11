"""Generate the configuration reference tables in docs/configuration.md.

Usage: python scripts/gen_config_reference.py [--check]
The tables are derived from the Pydantic models, so they cannot drift from the
code; tests/unit/test_docs.py runs this with --check.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from openmcp_gateway import config as c

DOC = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
BEGIN = "<!-- BEGIN GENERATED REFERENCE -->"
END = "<!-- END GENERATED REFERENCE -->"

SECTIONS: list[tuple[str, type[BaseModel]]] = [
    ("gateway", c.HttpConfig),
    ("auth", c.AuthConfig),
    ("auth.tokens[]", c.TokenConfig),
    ("admin", c.AdminConfig),
    ("limits", c.LimitsConfig),
    ("rate_limits", c.RateLimitConfig),
    ("logging", c.LoggingConfig),
    ("security", c.SecurityConfig),
    ("storage", c.StorageConfig),
    ("packages", c.PackagesConfig),
    ("packages.limits", c.PackageLimits),
    ("packages.artifacts", c.ArtifactStoreConfig),
    ("packages.catalog", c.CatalogConfig),
    ("packages.dependencies", c.DependencyPolicy),
    ("packages.sandbox", c.SandboxConfig),
    ("packages.retention", c.RetentionPolicy),
    ("servers[] (all transports)", c._ServerBase),
    ("servers[] with transport: stdio (default)", c.StdioServerConfig),
    ("servers[] with transport: http", c.HttpServerConfig),
    ("servers[] with transport: package", c.PackageServerConfig),
    ("servers[].timeouts", c.TimeoutConfig),
    ("servers[].restart", c.RestartPolicy),
    ("servers[].tools", c.ToolFilter),
]


def _type(annotation: Any) -> str:
    if get_origin(annotation) is None and hasattr(annotation, "__name__"):
        text = annotation.__name__
    else:
        text = str(annotation)
    text = text.replace("typing.", "").replace("pathlib.", "").replace("openmcp_gateway.config.", "")
    text = text.replace("pydantic.networks.", "")
    return text.replace("|", "\\|")


def _default(field: Any) -> str:
    if field.default_factory is not None:
        value = field.default_factory()
        if isinstance(value, BaseModel):
            return "(see section)"
        return f"`{value!r}`"
    if field.default is PydanticUndefined:
        return "**required**"
    return f"`{field.default!r}`" if not isinstance(field.default, str) else f'`"{field.default}"`'


def render() -> str:
    out: list[str] = []
    for title, model in SECTIONS:
        out.append(f"### `{title}`\n")
        out.append("| Key | Type | Default | Description |")
        out.append("| --- | --- | --- | --- |")
        base_fields = (
            set(c._ServerBase.model_fields)
            if model in (c.StdioServerConfig, c.HttpServerConfig, c.PackageServerConfig)
            else set()
        )
        for name, field in model.model_fields.items():
            if name in base_fields:
                continue
            desc = (field.description or "").replace("|", "\\|")
            if not desc and isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel):
                desc = f"See `{title.split(' ')[0]}.{name}`."
            out.append(f"| `{name}` | `{_type(field.annotation)}` | {_default(field)} | {desc} |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    text = DOC.read_text()
    start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
    updated = text[:start] + "\n\n" + render() + "\n" + text[end:]
    if "--check" in sys.argv:
        if updated != text:
            print("docs/configuration.md is out of date; run python scripts/gen_config_reference.py")
            return 1
        return 0
    DOC.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
