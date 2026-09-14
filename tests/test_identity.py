"""Identity/configuration resolution tests (design #162 §3.2, STANDARDS §2.3).

Proves SI-001 through SI-005: service.name/version precedence, the
independent override of the other resource-identity fields, the default
telemetry.sdk.* values, the Python 3.10 tomllib gap, and the no-raise
fallback when pyproject.toml is missing or unreadable.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semlog._identity import resolve_identity

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _write_pyproject(directory, text):
    (Path(directory) / "pyproject.toml").write_text(text, encoding="utf-8")


class IdentityResolutionTests(unittest.TestCase):
    """Proves: SI-001, SI-002, SI-003, SI-004, SI-005"""

    def test_explicit_service_name_wins_over_everything(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                "os.environ",
                {
                    "OTEL_SERVICE_NAME": "svc-env",
                    "OTEL_RESOURCE_ATTRIBUTES": "service.name=svc-attrs",
                },
                clear=True,
            ),
        ):
            _write_pyproject(tmp, '[project]\nname = "svc-pyproject"\n')
            identity = resolve_identity(service_name="svc-explicit", search_dir=tmp)
        self.assertEqual("svc-explicit", identity["service.name"])

    def test_env_var_overrides_pyproject_and_resource_attributes(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                "os.environ",
                {
                    "OTEL_SERVICE_NAME": "svc-b",
                    "OTEL_RESOURCE_ATTRIBUTES": "service.name=svc-attrs",
                },
                clear=True,
            ),
        ):
            _write_pyproject(tmp, '[project]\nname = "svc-a"\n')
            identity = resolve_identity(search_dir=tmp)
        self.assertEqual("svc-b", identity["service.name"])

    def test_resource_attributes_used_before_pyproject(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                "os.environ",
                {"OTEL_RESOURCE_ATTRIBUTES": "service.name=svc-attrs"},
                clear=True,
            ),
        ):
            _write_pyproject(tmp, '[project]\nname = "svc-pyproject"\n')
            identity = resolve_identity(search_dir=tmp)
        self.assertEqual("svc-attrs", identity["service.name"])

    @unittest.skipUnless(
        sys.version_info >= (3, 11), "pyproject.toml detection needs tomllib"
    )
    def test_pyproject_project_name_beats_poetry_name(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            _write_pyproject(
                tmp,
                '[project]\nname = "svc-pep621"\n\n'
                '[tool.poetry]\nname = "svc-poetry"\n',
            )
            identity = resolve_identity(search_dir=tmp)
        self.assertEqual("svc-pep621", identity["service.name"])

    @unittest.skipUnless(
        sys.version_info >= (3, 11), "pyproject.toml detection needs tomllib"
    )
    def test_poetry_name_used_when_project_name_absent(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            _write_pyproject(tmp, '[tool.poetry]\nname = "svc-poetry"\n')
            identity = resolve_identity(search_dir=tmp)
        self.assertEqual("svc-poetry", identity["service.name"])

    def test_default_service_name_when_nothing_resolves(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            identity = resolve_identity(search_dir=tmp)
        self.assertTrue(identity["service.name"].startswith("unknown_service:"))

    def test_service_version_falls_back_to_installed_package_metadata(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("semlog._identity.metadata.version", return_value="1.4.0"),
        ):
            identity = resolve_identity(service_name="svc-a", search_dir=tmp)
        self.assertEqual("1.4.0", identity["service.version"])

    def test_namespace_and_instance_id_override_independently(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            identity = resolve_identity(service_namespace="payments", search_dir=tmp)
        self.assertEqual("payments", identity["service.namespace"])
        self.assertTrue(_UUID_RE.match(identity["service.instance.id"]))

    def test_default_telemetry_sdk_identity(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            identity = resolve_identity(search_dir=tmp)
        self.assertEqual("semlog", identity["telemetry.sdk.name"])
        self.assertEqual("python", identity["telemetry.sdk.language"])

    def test_composed_identity_field(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            identity = resolve_identity(
                identity=("api", "payments"), namespace="app", search_dir=tmp
            )
        self.assertEqual("api.payments", identity["app.identity"])

    def test_identity_length_mismatch_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            resolve_identity(identity=("api",), search_dir=tmp)

    def test_python_310_skips_pyproject_detection(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("semlog._identity.sys.version_info", (3, 10, 0, "final", 0)),
        ):
            _write_pyproject(tmp, '[project]\nname = "svc-pyproject"\n')
            with self.assertLogs("semlog", level="INFO") as captured:
                identity = resolve_identity(search_dir=tmp)
        self.assertTrue(identity["service.name"].startswith("unknown_service:"))
        self.assertTrue(any("3.10" in message for message in captured.output))

    def test_missing_pyproject_falls_back_without_raising(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertLogs("semlog", level="INFO") as captured,
        ):
            identity = resolve_identity(search_dir=tmp)
        self.assertTrue(identity["service.name"].startswith("unknown_service:"))
        self.assertTrue(any("pyproject.toml" in message for message in captured.output))

    def test_unreadable_pyproject_falls_back_without_raising(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            _write_pyproject(tmp, "not [ valid toml")
            with self.assertLogs("semlog", level="INFO"):
                identity = resolve_identity(search_dir=tmp)
        self.assertTrue(identity["service.name"].startswith("unknown_service:"))

    def test_diagnostics_use_semlog_logger_namespace(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertLogs("semlog", level="INFO") as captured,
        ):
            resolve_identity(search_dir=tmp)
        self.assertEqual(1, len(captured.records))
        self.assertEqual("semlog", captured.records[0].name)


if __name__ == "__main__":
    unittest.main()
