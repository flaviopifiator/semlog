"""Service-identity resolution (STANDARDS.md §2.3, SI-001..005): one
precedence chain, never raising; returns a plain dotted-key `dict`, no
`Identity` class (engram #238; design #162 §4.1 order)."""

from __future__ import annotations

import logging
import os
import sys
import urllib.parse
import uuid
from importlib import metadata
from pathlib import Path

_DIAGNOSTIC_LOGGER = logging.getLogger("semlog")


def _parse_resource_attributes(raw: str) -> dict[str, str]:
    """Parse ``OTEL_RESOURCE_ATTRIBUTES`` (comma pairs, percent-decoded)."""
    attributes: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        attributes[key.strip()] = urllib.parse.unquote(value.strip())
    return attributes


def _read_pyproject(start: str | Path) -> tuple[str | None, str | None, str | None]:
    """Return ``(name, version, diagnostic)``; never raises (SI-005)."""
    if sys.version_info < (3, 11):
        return None, None, "pyproject.toml detection skipped: no tomllib on Python 3.10"
    here = Path(start).resolve()
    path = next(
        (
            candidate
            for directory in (here, *here.parents)
            if (candidate := directory / "pyproject.toml").is_file()
        ),
        None,
    )
    if path is None:
        return None, None, "no pyproject.toml found; falling back to defaults"
    import tomllib

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, None, "pyproject.toml could not be read; falling back"
    project = data.get("project", {})
    name = project.get("name") or data.get("tool", {}).get("poetry", {}).get("name")
    return name, project.get("version"), None


def _dist_version(name: str, default: str | None = "unknown") -> str | None:
    """Look up an installed distribution's version, or `default`."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return default


def resolve_identity(
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    service_namespace: str | None = None,
    service_instance_id: str | None = None,
    environment: str | None = None,
    identity: tuple[object, ...] | None = None,
    identity_levels: tuple[str, ...] = ("role", "component"),
    namespace: str = "app",
    search_dir: str | Path | None = None,
) -> dict[str, object]:
    """Resolve every resource-identity field (SI-001..005), design #162 §4.1 order."""
    if identity is not None and len(identity) != len(identity_levels):
        raise ValueError(
            f"identity has {len(identity)} values, identity_levels declares "
            f"{len(identity_levels)}"
        )

    resource_attrs = _parse_resource_attributes(
        os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    )
    resolved_name = (
        service_name
        or os.environ.get("OTEL_SERVICE_NAME")
        or resource_attrs.get("service.name")
    )
    pyproject_name = pyproject_version = None
    if not resolved_name:
        pyproject_name, pyproject_version, diagnostic = _read_pyproject(
            search_dir if search_dir is not None else Path.cwd()
        )
        if diagnostic:
            _DIAGNOSTIC_LOGGER.info(diagnostic)
        resolved_name = pyproject_name
    if not resolved_name:
        resolved_name = f"unknown_service:{Path(sys.argv[0]).name or 'python'}"

    resolved_version = (
        service_version
        or resource_attrs.get("service.version")
        or _dist_version(resolved_name, default=None)
        or pyproject_version
    )

    resolved: dict[str, object] = {
        "service.name": resolved_name,
        "service.namespace": service_namespace
        or resource_attrs.get("service.namespace"),
        "service.version": resolved_version,
        "service.instance.id": service_instance_id
        or resource_attrs.get("service.instance.id")
        or str(uuid.uuid4()),
        "deployment.environment.name": environment
        or resource_attrs.get("deployment.environment.name"),
        **{
            f"telemetry.sdk.{field}": resource_attrs.get(
                f"telemetry.sdk.{field}", default
            )
            for field, default in (
                ("name", "semlog"),
                ("version", _dist_version("semlog")),
                ("language", "python"),
            )
        },
    }
    if identity is not None:
        resolved = {
            f"{namespace}.identity": ".".join(str(v) for v in identity),
            **resolved,
        }
    return resolved
