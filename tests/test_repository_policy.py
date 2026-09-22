"""Repository-policy tests for the GitHub Actions workflows (tasks 4.1,
4.24-4.29, 4.32-4.33; STANDARDS.md PRV-001..006, OSF-001..004, CP-001,
CP-008, CP-009; design #223 section 5-7, 10; RELEASING.md).

No YAML library is approved (STANDARDS.md CP-008/CP-013), so this module
parses `.github/workflows/*.yml` with a small, restricted-style line
parser: block-style mappings/sequences, two-space indentation, no
anchors, aliases, or tabs -- exactly the shape every workflow file in
this repository is written in. The parser never imports or executes any
workflow content; it only scans text with `re` and simple indentation
bookkeeping, the same discipline `tests/test_traceability.py` and
`tests/test_doc_conformance.py` already use for their own Markdown/TOML
parsing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ._readme_support import READMES

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_PATH = WORKFLOWS_DIR / "ci.yml"
RELEASE_PATH = WORKFLOWS_DIR / "release.yml"
TAG_PATH = WORKFLOWS_DIR / "tag.yml"
VERSION_CHECK_PATH = WORKFLOWS_DIR / "version-check.yml"
UV_REQUIREMENTS_PATH = REPO_ROOT / ".github" / "uv-requirements.txt"
SECURITY_PATH = REPO_ROOT / "SECURITY.md"
LICENSE_PATH = REPO_ROOT / "LICENSE"
STANDARDS_PATH = REPO_ROOT / "STANDARDS.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
RELEASING_PATH = REPO_ROOT / "RELEASING.md"

APPROVED_ACTIONS = frozenset(
    {
        "actions/checkout",
        "actions/setup-python",
        "actions/upload-artifact",
        "actions/download-artifact",
        "pypa/gh-action-pypi-publish",
    }
)

# Every workflow file these tests have reviewed. A new workflow must be added
# here deliberately, so no file escapes the policy checks below.
REVIEWED_WORKFLOWS = ("ci.yml", "release.yml", "tag.yml", "version-check.yml")

# PyPI Trusted Publishing does not support publishing from a reusable
# workflow (docs.pypi.org/trusted-publishers/troubleshooting/), so each
# workflow that publishes holds its own top-level `publish` job and is
# registered on PyPI as its own trusted publisher (RELEASING.md).
PUBLISHING_WORKFLOWS = ("release.yml", "tag.yml")

# The only write permissions anywhere: `id-token: write` for each publish
# job (PRV-001/PRV-002) and `contents: write` for the job that pushes the
# release tag (PRV-006).
EXPECTED_WRITE_PERMISSIONS = {
    ("release.yml", "publish"): ["id-token"],
    ("tag.yml", "publish"): ["id-token"],
    ("tag.yml", "tag"): ["contents"],
}

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s+#\s*(.*?))?\s*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_COMMENT_RE = re.compile(r"^v[0-9][\w.+-]*$")
_TOP_PERMISSIONS_EMPTY_RE = re.compile(r"(?m)^permissions:\s*\{\}\s*$")
_JOB_HEADER_RE = re.compile(r"^ {2}([\w-]+):\s*$")
_JOB_PERMISSIONS_RE = re.compile(r"^ {4}permissions:")
_ID_TOKEN_WRITE_RE = re.compile(r"^\s*id-token:\s*write\s*$")
_WRITE_PERMISSION_RE = re.compile(r"^\s*([\w-]+):\s*write\s*$")
_WRITE_ALL_RE = re.compile(r"^\s*permissions:\s*write-all\s*$")
_STEP_START_RE = re.compile(r"^ {6}- ")
_RUN_SCALAR_RE = re.compile(r"^(\s*(?:-\s*)?)run:\s*(.*)$")
_BLOCK_SCALAR_INDICATORS = frozenset({"|", ">", "|-", ">-", "|+", ">+"})
_BLOCK_SCALAR_HEADER_RE = re.compile(r"^[|>](?:[1-9][-+]?|[-+][1-9]?)?$")
# YAML 1.2 section 5.3 indicator characters; a plain scalar must not start
# with one (quoted and block scalars are recognized before this check).
_YAML_INDICATOR_CHARS = frozenset("-?:,[]{}#&*!|>'\"%@`")
_COMMENT_START_RE = re.compile(r"\s#")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_uses_lines(text: str) -> list[dict]:
    """One entry per `uses:` line: a job-level reusable-workflow call
    (``uses: ./path``, no ``@``/SHA/comment expected) or a step-level
    external action reference (``uses: owner/repo@<ref> # <comment>``)."""
    entries = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _USES_RE.match(line)
        if not match:
            continue
        raw_ref, comment = match.group(1), match.group(2)
        if raw_ref.startswith("./"):
            entries.append(
                {
                    "line_no": line_no,
                    "local": True,
                    "owner_repo": None,
                    "ref": raw_ref,
                    "comment": comment,
                }
            )
            continue
        owner_repo, _, ref = raw_ref.partition("@")
        entries.append(
            {
                "line_no": line_no,
                "local": False,
                "owner_repo": owner_repo,
                "ref": ref or None,
                "comment": comment,
            }
        )
    return entries


def find_unapproved_actions(uses_entries):
    """Proves: CP-008"""
    return sorted(
        {
            e["owner_repo"]
            for e in uses_entries
            if not e["local"] and e["owner_repo"] not in APPROVED_ACTIONS
        }
    )


def find_unpinned_actions(uses_entries):
    """Actions not pinned by a full 40-hex commit SHA (OSF-002)."""
    return sorted(
        f"{e['owner_repo']}@{e['ref']}"
        for e in uses_entries
        if not e["local"] and not (e["ref"] and _SHA_RE.match(e["ref"]))
    )


def find_missing_version_comments(uses_entries):
    """Pinned SHAs without a trailing `# vX.Y.Z` comment naming the
    version they resolve to (AGENTS.md's pinning-policy convention)."""
    return sorted(
        f"{e['owner_repo']}@{e['ref']}"
        for e in uses_entries
        if not e["local"]
        and not (e["comment"] and _VERSION_COMMENT_RE.match(e["comment"].strip()))
    )


def find_inconsistent_pins(uses_entries):
    """Actions referenced with more than one `SHA # version` pin, so a new
    reference cannot silently introduce a second, unreviewed revision."""
    pins: dict[str, set[str]] = {}
    for e in uses_entries:
        if not e["local"]:
            pins.setdefault(e["owner_repo"], set()).add(f"{e['ref']} # {e['comment']}")
    return sorted(action for action, refs in pins.items() if len(refs) > 1)


def top_level_permissions_is_empty(text: str) -> bool:
    return _TOP_PERMISSIONS_EMPTY_RE.search(text) is not None


def job_blocks(text: str) -> dict[str, str]:
    """Return ``{job_name: block_text}`` for every direct child of a
    top-level ``jobs:`` key (two-space indent); each block spans until
    the next two-space-indent job header or a dedent back to column 0."""
    lines = text.splitlines()
    try:
        jobs_at = lines.index("jobs:")
    except ValueError:
        return {}
    headers = []
    for i in range(jobs_at + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t"):
            break
        match = _JOB_HEADER_RE.match(line)
        if match:
            headers.append((match.group(1), i))
    blocks = {}
    for idx, (name, start) in enumerate(headers):
        end = headers[idx + 1][1] if idx + 1 < len(headers) else len(lines)
        blocks[name] = "\n".join(lines[start:end])
    return blocks


def step_blocks(job_block: str) -> list[str]:
    """Return the text of each step (six-space-indent ``- `` item) in a job."""
    steps: list[list[str]] = []
    in_steps = False
    for line in job_block.splitlines():
        if line.startswith("    steps:"):
            in_steps = True
            continue
        if not in_steps:
            continue
        if _STEP_START_RE.match(line):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return ["\n".join(step) for step in steps]


def job_steps_text(job_block: str) -> str:
    """The ``steps:`` part of a job, which excludes `needs:`/`if:`."""
    return "\n".join(step_blocks(job_block))


def find_jobs_missing_permissions(text: str) -> list[str]:
    """Proves: PRV-002, OSF-002"""
    return sorted(
        name
        for name, block in job_blocks(text).items()
        if not any(_JOB_PERMISSIONS_RE.match(line) for line in block.splitlines())
    )


def find_jobs_with_id_token_write(text: str) -> list[str]:
    """Proves: PRV-002"""
    return sorted(
        name
        for name, block in job_blocks(text).items()
        if any(_ID_TOKEN_WRITE_RE.match(line) for line in block.splitlines())
    )


def find_write_permissions(text: str) -> dict[str, list[str]]:
    """``{job_name: [scope, ...]}`` for every job granting any ``write``
    scope (or ``write-all``) anywhere in its block."""
    found = {}
    for name, block in job_blocks(text).items():
        scopes = []
        for line in block.splitlines():
            if _WRITE_ALL_RE.match(line):
                scopes.append("write-all")
                continue
            match = _WRITE_PERMISSION_RE.match(line)
            if match:
                scopes.append(match.group(1))
        if scopes:
            found[name] = sorted(scopes)
    return found


def checkout_inputs(text: str, key: str) -> dict[str, list[str | None]]:
    """``{job_name: [value, ...]}`` of one `with:` input (``key``), one entry
    per `actions/checkout` step (``None`` when the step leaves the default)."""
    value_re = re.compile(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", re.MULTILINE)
    found = {}
    for name, block in job_blocks(text).items():
        values = []
        for step in step_blocks(block):
            if "uses: actions/checkout@" not in step:
                continue
            match = value_re.search(step)
            values.append(match.group(1) if match else None)
        if values:
            found[name] = values
    return found


def checkout_credentials(text: str) -> dict[str, list[str | None]]:
    """``{job_name: [persist-credentials value, ...]}`` per checkout step."""
    return checkout_inputs(text, "persist-credentials")


def find_plain_run_scalar_hazards(text: str) -> list[int]:
    """Line numbers of single-line `run:` values YAML would not read as the
    intended command. A plain (unquoted) scalar must not start with a YAML
    indicator character, contain `: ` (it starts a mapping, which makes the
    whole file invalid), end with `:`, or contain ` #` (it starts a comment
    that silently truncates the command). A quoted scalar must close on its
    own line, and an empty value is never a command. Block scalars
    (`run: |`) are exempt: their content is literal text."""
    hazards = []
    in_block = False
    block_indent = -1
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if in_block:
            if indent > block_indent:
                continue
            in_block = False
        match = _RUN_SCALAR_RE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if _BLOCK_SCALAR_HEADER_RE.match(value):
            in_block = True
            block_indent = len(match.group(1))
            continue
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                hazards.append(line_no)
            continue
        if (
            not value
            or value[0] in _YAML_INDICATOR_CHARS
            or ": " in value
            or value.endswith(":")
            or _COMMENT_START_RE.search(value)
        ):
            hazards.append(line_no)
    return hazards


def find_run_expression_violations(text: str) -> list[int]:
    """Line numbers of any `run:` scalar (single-line or block-style)
    containing a `${{ }}` GitHub Actions expression (AGENTS.md's
    injection-hardening rule: such values must pass through `env:`
    instead)."""
    violations = []
    in_block = False
    block_indent = -1
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if in_block:
            if indent > block_indent:
                if "${{" in line:
                    violations.append(line_no)
                continue
            in_block = False
        match = _RUN_SCALAR_RE.match(line)
        if match:
            rest = match.group(2).strip()
            if rest in _BLOCK_SCALAR_INDICATORS:
                in_block = True
                block_indent = len(match.group(1))
                continue
            if "${{" in rest:
                violations.append(line_no)
    return violations


class _WorkflowTextMixin:
    @classmethod
    def setUpClass(cls):
        cls.workflows = {
            path.name: read_text(path) for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        }
        cls.ci_text = read_text(CI_PATH)
        cls.release_text = read_text(RELEASE_PATH)
        cls.tag_text = read_text(TAG_PATH)
        cls.version_check_text = read_text(VERSION_CHECK_PATH)
        cls.ci_uses = parse_uses_lines(cls.ci_text)
        cls.release_uses = parse_uses_lines(cls.release_text)
        cls.all_uses = [e for t in cls.workflows.values() for e in parse_uses_lines(t)]


class WorkflowInventoryTests(_WorkflowTextMixin, unittest.TestCase):
    """Every workflow file on disk is one these policy tests were written for."""

    def test_the_workflow_set_is_exactly_the_reviewed_one(self):
        self.assertEqual(sorted(REVIEWED_WORKFLOWS), sorted(self.workflows))


class ActionAllowlistAndPinningTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: OSF-002, CP-008"""

    def test_only_approved_actions_are_referenced(self):
        self.assertEqual([], find_unapproved_actions(self.all_uses))

    def test_every_external_action_is_pinned_by_full_commit_sha(self):
        self.assertEqual([], find_unpinned_actions(self.all_uses))

    def test_every_external_action_carries_a_version_comment(self):
        self.assertEqual([], find_missing_version_comments(self.all_uses))

    def test_each_action_is_pinned_to_one_sha_across_all_workflows(self):
        self.assertEqual([], find_inconsistent_pins(self.all_uses))


class PermissionsLeastPrivilegeTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-002, PRV-006, OSF-002"""

    def test_top_level_permissions_are_empty_in_every_workflow(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertTrue(top_level_permissions_is_empty(text))

    def test_every_job_declares_its_own_permissions(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertEqual([], find_jobs_missing_permissions(text))

    def test_id_token_write_is_scoped_to_the_publish_jobs_only(self):
        found = sorted(
            (name, job)
            for name, text in self.workflows.items()
            for job in find_jobs_with_id_token_write(text)
        )
        self.assertEqual([(name, "publish") for name in PUBLISHING_WORKFLOWS], found)

    def test_the_only_write_permissions_are_the_expected_ones(self):
        found = {
            (name, job): scopes
            for name, text in self.workflows.items()
            for job, scopes in find_write_permissions(text).items()
        }
        self.assertEqual(EXPECTED_WRITE_PERMISSIONS, found)


class CheckoutCredentialsTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-006, OSF-002"""

    def test_only_the_tag_job_checkout_keeps_credentials(self):
        found = {
            (name, job): values
            for name, text in self.workflows.items()
            for job, values in checkout_credentials(text).items()
        }
        self.assertEqual(["true"], found.pop(("tag.yml", "tag")))
        self.assertTrue(found, "no other checkout step found")
        for key, values in sorted(found.items()):
            with self.subTest(job=key):
                self.assertEqual(["false"] * len(values), values)


class NoRunStepExpressionInjectionTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: OSF-002"""

    def test_no_run_step_interpolates_a_github_expression(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertEqual([], find_run_expression_violations(text))


class RunScalarYamlSafetyTests(unittest.TestCase):
    """Every workflow must be valid YAML, or GitHub never runs it. No YAML
    library is approved (CP-008/CP-013), so this guards the one hazard the
    line parser can see: a single-line `run:` value that YAML reads as a
    mapping, a comment, or another node instead of the command. Not tied to
    a single STANDARDS.md requirement id (same precedent as
    `UvBootstrapHashPinningTests`: no id cleanly backs this specific check
    alone), so this carries no `Proves:` line."""

    def test_no_single_line_run_value_is_a_yaml_hazard(self):
        paths = sorted(WORKFLOWS_DIR.glob("*.yml"))
        self.assertGreaterEqual(len(paths), 2, "no workflow files found")
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual([], find_plain_run_scalar_hazards(read_text(path)))


class ReleaseTagTriggerAndPublicGuardTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-002"""

    _RELEASE_TRIGGER_RE = re.compile(
        r'(?m)^on:\n  push:\n    tags:\n      - "v\*"\n'
        r"  workflow_dispatch:\n    inputs:\n      tag:\n"
        r"        description: \S.*\n        required: true\n        type: string\n"
        r"\npermissions:"
    )
    _MAIN_PUSH_TRIGGER_RE = re.compile(
        r"(?m)^on:\n  push:\n    branches: \[main\]\n\npermissions:"
    )

    def test_release_triggers_on_a_tag_push_or_a_dispatch_with_a_required_tag(self):
        self.assertRegex(self.release_text, self._RELEASE_TRIGGER_RE)

    def test_manual_dispatch_is_allowed_only_in_release_yml(self):
        found = sorted(
            name for name, text in self.workflows.items() if "workflow_dispatch" in text
        )
        self.assertEqual(["release.yml"], found)

    def test_tag_workflow_triggers_only_on_a_push_to_main(self):
        self.assertRegex(self.tag_text, self._MAIN_PUSH_TRIGGER_RE)

    def test_publish_jobs_guard_on_a_public_repository(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                publish_block = job_blocks(self.workflows[name]).get("publish", "")
                self.assertRegex(
                    publish_block,
                    r"(?m)^    if: .*github\.event\.repository\.private == false$",
                )

    def test_publish_jobs_use_the_dedicated_pypi_environment(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                publish_block = job_blocks(self.workflows[name]).get("publish", "")
                self.assertRegex(publish_block, r"environment:\s*\n\s*name:\s*pypi")


class TrustedPublishingActionTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-001"""

    def test_pypi_publish_action_is_pinned_once_per_publishing_workflow(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                refs = [
                    e
                    for e in parse_uses_lines(text)
                    if e["owner_repo"] == "pypa/gh-action-pypi-publish"
                ]
                expected = 1 if name in PUBLISHING_WORKFLOWS else 0
                self.assertEqual(expected, len(refs))
                for ref in refs:
                    self.assertTrue(_SHA_RE.match(ref["ref"]))

    def test_publish_action_runs_only_in_a_top_level_publish_job(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                text = self.workflows[name]
                self.assertNotIn("workflow_call", text)
                publish_block = job_blocks(text).get("publish", "")
                self.assertIn("uses: pypa/gh-action-pypi-publish@", publish_block)

    def test_publish_jobs_run_no_repository_code(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                steps = step_blocks(job_blocks(self.workflows[name]).get("publish", ""))
                actions = [re.search(r"uses: ([\w-]+/[\w-]+)@", s) for s in steps]
                self.assertEqual(
                    ["actions/download-artifact", "pypa/gh-action-pypi-publish"],
                    [m.group(1) if m else None for m in actions],
                )

    def test_attestations_are_not_explicitly_disabled(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertNotRegex(self.workflows[name], r"attestations:\s*false")

    def test_no_long_lived_pypi_token_is_stored_or_referenced(self):
        for name, text in self.workflows.items():
            for forbidden in ("password:", "secrets.", "TWINE_", "pypi-token"):
                with self.subTest(workflow=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)


class PublishingWorkflowsDoNotDriftTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-001, PRV-004

    `tag.yml` cannot call `release.yml` to publish (PyPI does not support
    Trusted Publishing from a reusable workflow), so both files carry the
    same build and publish steps. They must stay identical, except for the
    expressions that say which commit the build checks out and which tag it
    verifies."""

    _EXPRESSION_VALUE_RE = re.compile(
        r"(?m)^( +(?:RELEASE_TAG|ref): )\$\{\{ [^}]+ \}\}$"
    )

    def _normalized_steps(self, name: str, job: str) -> str:
        steps = job_steps_text(job_blocks(self.workflows[name]).get(job, ""))
        return self._EXPRESSION_VALUE_RE.sub(r"\1<expression>", steps)

    def test_build_and_publish_steps_match_between_publishing_workflows(self):
        for job in ("build", "publish"):
            with self.subTest(job=job):
                release_steps = self._normalized_steps("release.yml", job)
                self.assertTrue(release_steps, f"release.yml has no {job} steps")
                self.assertEqual(release_steps, self._normalized_steps("tag.yml", job))

    def test_build_jobs_verify_the_tag_before_building(self):
        self.assertIn(
            "RELEASE_TAG: ${{ inputs.tag || github.ref_name }}",
            job_blocks(self.release_text)["build"],
        )
        self.assertIn(
            "RELEASE_TAG: ${{ needs.plan.outputs.tag }}",
            job_blocks(self.tag_text)["build"],
        )
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                build = job_blocks(self.workflows[name])["build"]
                verify_at = build.index("python tools/release_policy.py verify-tag")
                self.assertLess(verify_at, build.index("uv build"))


class ManualReleaseDispatchTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-002

    A maintainer can publish a tag that already exists (for example one
    created while the repository was private) by dispatching `release.yml`
    with that tag. Every job must then test and build the tag's commit, not
    the branch the dispatch ran from."""

    def test_release_ci_and_build_check_out_the_dispatched_tag(self):
        jobs = job_blocks(self.release_text)
        self.assertRegex(
            jobs["ci"], r"(?m)^    with:\n      ref: \$\{\{ inputs\.tag \}\}$"
        )
        self.assertEqual(
            ["${{ inputs.tag }}"], checkout_inputs(self.release_text, "ref")["build"]
        )

    def test_ci_workflow_checks_out_the_ref_its_caller_requests(self):
        self.assertRegex(
            self.ci_text,
            r"(?m)^  workflow_call:\n    inputs:\n      ref:\n"
            r"        description: \S.*\n        required: false\n"
            r'        type: string\n        default: ""$',
        )
        refs = checkout_inputs(self.ci_text, "ref")
        self.assertEqual(
            [
                "aiohttp-matrix",
                "build-wheel",
                "django-matrix",
                "fastapi-matrix",
                "lint",
                "test",
            ],
            sorted(refs),
        )
        for job, values in sorted(refs.items()):
            with self.subTest(job=job):
                self.assertEqual(["${{ inputs.ref }}"], values)

    def test_tag_workflow_builds_exactly_the_commit_it_tags(self):
        self.assertEqual(
            ["${{ github.sha }}"], checkout_inputs(self.tag_text, "ref")["build"]
        )


class TagWorkflowTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-006"""

    def setUp(self):
        self.jobs = job_blocks(self.tag_text)

    def test_jobs_run_in_release_order(self):
        self.assertEqual(["plan", "ci", "build", "tag", "publish"], list(self.jobs))
        self.assertRegex(self.jobs["ci"], r"(?m)^    needs: plan$")
        self.assertRegex(self.jobs["build"], r"(?m)^    needs: \[plan, ci\]$")
        self.assertRegex(self.jobs["tag"], r"(?m)^    needs: \[plan, build\]$")
        self.assertRegex(self.jobs["publish"], r"(?m)^    needs: \[build, tag\]$")

    def test_ci_runs_only_when_a_tag_is_needed(self):
        self.assertIn("uses: ./.github/workflows/ci.yml", self.jobs["ci"])
        self.assertRegex(
            self.jobs["ci"], r"(?m)^    if: needs\.plan\.outputs\.needed == 'true'$"
        )

    def test_publish_runs_only_when_this_run_created_the_tag(self):
        self.assertRegex(
            self.jobs["publish"],
            r"(?m)^    if: needs\.tag\.outputs\.created == 'true' && ",
        )

    def test_plan_reads_the_tag_from_the_policy_tool_and_skips_existing_tags(self):
        plan = self.jobs["plan"]
        self.assertIn(
            'python tools/release_policy.py tag-info >> "$GITHUB_OUTPUT"', plan
        )
        self.assertIn("fetch-depth: 0", plan)
        self.assertIn('git rev-parse --quiet --verify "refs/tags/$TAG"', plan)
        self.assertIn('echo "needed=false" >> "$GITHUB_OUTPUT"', plan)
        self.assertIn("already exists", plan)

    def test_tag_job_rechecks_the_remote_and_exits_cleanly_when_the_tag_exists(self):
        tag = self.jobs["tag"]
        self.assertIn('git ls-remote --exit-code --tags origin "refs/tags/$TAG"', tag)
        self.assertIn('echo "created=false" >> "$GITHUB_OUTPUT"', tag)
        self.assertIn("already exists", tag)
        self.assertRegex(tag, r'(?m)^ +exit "\$status"$')

    def test_tag_job_pushes_an_annotated_tag_with_the_changelog_message(self):
        tag = self.jobs["tag"]
        self.assertIn("TAG_MESSAGE: ${{ needs.plan.outputs.message }}", tag)
        self.assertIn(
            'git tag --annotate --cleanup=verbatim --file "$RUNNER_TEMP/tag-message.txt"'
            ' "$TAG" "$GITHUB_SHA"',
            tag,
        )
        self.assertIn('git push origin "refs/tags/$TAG"', tag)
        self.assertIn('echo "created=true" >> "$GITHUB_OUTPUT"', tag)

    def test_tag_job_acts_as_the_github_actions_bot(self):
        tag = self.jobs["tag"]
        self.assertIn('git config user.name "github-actions[bot]"', tag)
        self.assertIn(
            'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"',
            tag,
        )

    def test_the_job_with_write_access_runs_no_repository_code(self):
        tag = self.jobs["tag"]
        for forbidden in ("python", "uv ", "tools/", "setup-python"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, tag)

    def test_tag_job_serializes_concurrent_runs_without_cancelling(self):
        tag = self.jobs["tag"]
        self.assertRegex(tag, r"(?m)^    concurrency:\n      group: \S+\n")
        self.assertIn("cancel-in-progress: false", tag)


class VersionCheckWorkflowTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-005"""

    _PR_TRIGGER_RE = re.compile(
        r"(?m)^on:\n  pull_request:\n    branches: \[main\]\n\npermissions:"
    )

    def setUp(self):
        self.jobs = job_blocks(self.version_check_text)

    def test_runs_on_pull_requests_to_main_with_read_only_access(self):
        self.assertRegex(self.version_check_text, self._PR_TRIGGER_RE)
        self.assertEqual({}, find_write_permissions(self.version_check_text))
        self.assertEqual(["version-check"], list(self.jobs))
        self.assertRegex(
            self.jobs["version-check"], r"(?m)^    permissions:\n      contents: read$"
        )

    def test_fetches_full_history_and_passes_shas_through_env(self):
        job = self.jobs["version-check"]
        self.assertIn("fetch-depth: 0", job)
        self.assertIn("BASE_SHA: ${{ github.event.pull_request.base.sha }}", job)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", job)

    def test_checks_commits_since_the_latest_release_tag_with_the_policy_tool(self):
        job = self.jobs["version-check"]
        for fragment in (
            "git tag --list 'v*' > \"$RUNNER_TEMP/tags.txt\"",
            (
                'released="$(python tools/release_policy.py latest-tag'
                ' --tags "$RUNNER_TEMP/tags.txt")"'
            ),
            'git log --format=%B%x00 "$range" > "$RUNNER_TEMP/commits.txt"',
            'git show "$BASE_SHA:pyproject.toml"',
            'git show "$HEAD_SHA:pyproject.toml"',
            'git show "$HEAD_SHA:CHANGELOG.md"',
            "python tools/release_policy.py check-pr",
            '--tags "$RUNNER_TEMP/tags.txt"',
            '--commits "$RUNNER_TEMP/commits.txt"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, job)

    def test_range_starts_at_the_release_tag_or_the_base_before_any_release(self):
        self.assertRegex(
            self.jobs["version-check"],
            r'if \[ -n "\$released" \]; then\n +range="refs/tags/\$released\.\.\$HEAD_SHA"'
            r'\n +else\n +range="\$BASE_SHA\.\.\$HEAD_SHA"\n +fi',
        )


class BuildFrontendTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-004"""

    def test_build_jobs_run_uv_build(self):
        for name in PUBLISHING_WORKFLOWS:
            with self.subTest(workflow=name):
                build_block = job_blocks(self.workflows[name]).get("build", "")
                self.assertIn("uv build", build_block)

    def test_uv_publish_is_never_used(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertNotIn("uv publish", text)


class PythonVersionMatrixTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: CP-001"""

    def test_test_job_matrix_covers_3_10_through_3_15(self):
        match = re.search(r"(?m)^\s*python:\s*\[([^\]]*)\]", self.ci_text)
        self.assertIsNotNone(match, "no python matrix found in ci.yml")
        versions = [v.strip().strip('"') for v in match.group(1).split(",")]
        self.assertEqual(["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"], versions)

    def test_3_15_is_allowed_to_fail(self):
        self.assertIn("continue-on-error: ${{ matrix.python == '3.15' }}", self.ci_text)


# Each compatibility-matrix job and the one test module it runs.
MATRIX_JOB_TEST_MODULES = {
    "aiohttp-matrix": "tests.test_aiohttp_middleware",
    "django-matrix": "tests.test_django_matrix",
    "fastapi-matrix": "tests.test_fastapi_matrix",
}

_MATRIX_ROW_RE = re.compile(r'(?m)^ {10}- python: "([^"]+)"\n {12}aiohttp: "([^"]+)"$')


class CompatibilityMatrixJobsTests(_WorkflowTextMixin, unittest.TestCase):
    """Every compatibility-matrix job in `ci.yml` keeps the same shape: a
    least-privilege `contents: read`, a `fail-fast: false` include list
    holding a floor row and the latest rows, the pinned version reaching
    the command through `env:` rather than a `${{ }}` expression inside
    `run:`, and exactly one `uv run --with` invocation naming only that
    framework's own test module (the default `test` job never installs a
    framework, so the module skips there).

    Supporting checks, no `Proves` tag: CP-001, CP-002 and CP-009 are
    proven by their own modules; this one keeps the three jobs from
    drifting apart as one is added."""

    def setUp(self):
        self.jobs = job_blocks(self.ci_text)

    def test_every_matrix_job_runs_only_its_own_test_module(self):
        for job, module in sorted(MATRIX_JOB_TEST_MODULES.items()):
            with self.subTest(job=job):
                self.assertIn(job, self.jobs)
                block = self.jobs[job]
                self.assertIn(f"python -m unittest {module} -v", block)
                self.assertNotIn("unittest discover", block)

    def test_every_matrix_job_is_least_privilege_and_does_not_fail_fast(self):
        for job in sorted(MATRIX_JOB_TEST_MODULES):
            with self.subTest(job=job):
                block = self.jobs[job]
                self.assertIn("runs-on: ubuntu-24.04", block)
                self.assertRegex(block, r"(?m)^    permissions:\n      contents: read$")
                self.assertRegex(block, r"(?m)^      fail-fast: false$")
                self.assertIn("python-version: ${{ matrix.python }}", block)
                self.assertIn("uv sync --locked", block)

    def test_the_aiohttp_job_pins_a_floor_row_and_the_latest_rows(self):
        rows = _MATRIX_ROW_RE.findall(self.jobs["aiohttp-matrix"])
        self.assertEqual(
            [("3.10", "3.10.0"), ("3.10", "3.14.3"), ("3.14", "3.14.3")], rows
        )

    def test_the_aiohttp_version_reaches_the_command_through_env(self):
        block = self.jobs["aiohttp-matrix"]
        self.assertIn("AIOHTTP_VERSION: ${{ matrix.aiohttp }}", block)
        self.assertIn('uv run --with "aiohttp==$AIOHTTP_VERSION"', block)
        for line in block.splitlines():
            if re.match(r"^\s*(- )?run:", line):
                with self.subTest(line=line.strip()):
                    self.assertNotIn("${{", line)


class UnittestOnlyInCiTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: CP-009"""

    def test_test_job_runs_unittest_discover(self):
        test_block = job_blocks(self.ci_text).get("test", "")
        self.assertIn("python -m unittest discover", test_block)

    def test_no_workflow_ever_invokes_pytest(self):
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertNotRegex(text, r"\bpytest\b")


class ScorecardAndBadgeNotAutomatedTests(unittest.TestCase):
    """Proves: OSF-003, OSF-004"""

    def test_no_scorecard_action_anywhere_under_workflows(self):
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            with self.subTest(path=path.name):
                self.assertNotIn("ossf/scorecard-action", read_text(path))

    def test_no_automated_bestpractices_submission(self):
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            with self.subTest(path=path.name):
                self.assertNotIn("bestpractices.dev", read_text(path))


class PreReleaseCriteriaDocumentedTests(unittest.TestCase):
    """Proves: OSF-001"""

    def test_security_md_documents_the_14_day_response_and_private_reporting(self):
        text = read_text(SECURITY_PATH)
        self.assertIn("14 days", text)
        self.assertIn("private vulnerability", text)

    def test_license_is_apache_2_0(self):
        text = read_text(LICENSE_PATH)
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)

    def test_agents_md_documents_the_automated_test_suite_command(self):
        self.assertIn("python -m unittest discover", read_text(AGENTS_PATH))


class VerificationDocsNoOverclaimTests(unittest.TestCase):
    """Proves: PRV-003

    Release verification is documented in SECURITY.md's "Verifying
    releases" section, not in the READMEs."""

    _SLSA_LEVEL_CLAIM_RE = re.compile(r"(?i)SLSA[^\n]{0,40}(?:level|nivel|L)\s*\d")
    _NO_AUTOMATIC_VERIFICATION = (
        "`pip` and `uv` do not verify these attestations automatically at install time"
    )

    def test_security_md_gives_a_concrete_attestation_verification_command(self):
        text = read_text(SECURITY_PATH)
        self.assertIn("## Verifying releases", text)
        self.assertIn("pypi-attestations verify pypi", text)
        self.assertIn("pypi:semlog-", text)

    def test_security_md_states_no_automatic_verification_at_install(self):
        prose = " ".join(read_text(SECURITY_PATH).split())
        self.assertIn(self._NO_AUTOMATIC_VERIFICATION, prose)

    def test_no_slsa_level_is_claimed_anywhere(self):
        paths = [labels["path"] for labels in READMES.values()] + [
            SECURITY_PATH,
            STANDARDS_PATH,
            RELEASING_PATH,
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotRegex(read_text(path), self._SLSA_LEVEL_CLAIM_RE)


def branch_protection_section(text: str) -> str:
    start = text.index("## Branch protection")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


BRANCH_PROTECTION_ELEMENTS = (
    "ruleset on `main`",
    "pull request",
    "`ci` checks",
    "force pushes",
    "branch deletion",
    "`v*` tags",
    "`pypi` environment",
    "`GITHUB_TOKEN`",
    "read-only",
)


class BranchProtectionDocumentedTests(unittest.TestCase):
    """Proves: OSF-002

    The branch protection rules are documented in the repository, in
    AGENTS.md's "Branch protection" section (moved there from the README):
    the `main` ruleset, the `v*` tag ruleset, the `pypi` environment, and
    the read-only default `GITHUB_TOKEN`."""

    def test_agents_md_documents_the_branch_protection_rules(self):
        documented = branch_protection_section(read_text(AGENTS_PATH))
        missing = [e for e in BRANCH_PROTECTION_ELEMENTS if e not in documented]
        self.assertEqual([], missing)

    def test_a_dropped_rule_is_caught(self):
        documented = branch_protection_section(read_text(AGENTS_PATH))
        mutated = documented.replace("force pushes", "rewrites")
        self.assertNotEqual(mutated, documented)
        self.assertIn(
            "force pushes", [e for e in BRANCH_PROTECTION_ELEMENTS if e not in mutated]
        )


class ReleasingDocumentationTests(_WorkflowTextMixin, unittest.TestCase):
    """Proves: PRV-005, PRV-006

    RELEASING.md is the maintainer's runbook: it must name every workflow
    file that has to be registered on PyPI as a trusted publisher, the
    protected `pypi` environment, and the commands the workflows run, and
    AGENTS.md must point to it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.releasing = read_text(RELEASING_PATH)

    def test_names_every_publishing_workflow_and_the_pypi_environment(self):
        publishing = sorted(
            name
            for name, text in self.workflows.items()
            if "pypa/gh-action-pypi-publish" in text
        )
        self.assertEqual(sorted(PUBLISHING_WORKFLOWS), publishing)
        for name in publishing:
            with self.subTest(workflow=name):
                self.assertIn(f"`{name}`", self.releasing)
        self.assertIn("`pypi`", self.releasing)
        self.assertIn("Required reviewers", self.releasing)

    def test_documents_the_policy_commands_the_workflows_run(self):
        for command in ("latest-tag", "check-pr", "tag-info", "verify-tag"):
            with self.subTest(command=command):
                self.assertIn(f"release_policy.py {command}", self.releasing)

    def test_documents_the_release_baseline_and_publishing_an_existing_tag(self):
        for phrase in (
            "since the last release tag",
            "Run workflow",
            "workflow_dispatch",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.releasing)

    def test_agents_md_points_to_releasing_md(self):
        self.assertIn("](RELEASING.md)", read_text(AGENTS_PATH))


class UvBootstrapHashPinningTests(_WorkflowTextMixin, unittest.TestCase):
    """Pinned, hash-checked `uv` CLI bootstrap for CI (design #223
    P3-ADR-8). Not tied to a single STANDARDS.md requirement id:
    hash-pinning the `uv` bootstrap backs PRV-002/OSF-002's SHA/
    least-privilege posture but is not itself a separately declared rule,
    so this carries no `Proves:` line."""

    def test_uv_requirements_file_pins_an_exact_version_with_hashes(self):
        text = read_text(UV_REQUIREMENTS_PATH)
        self.assertRegex(text, r"(?m)^uv==[\w.]+")
        self.assertGreaterEqual(text.count("--hash=sha256:"), 1)

    def test_ci_installs_uv_with_require_hashes(self):
        self.assertIn(
            "pip install --require-hashes --only-binary=:all: --no-deps "
            "-r .github/uv-requirements.txt",
            read_text(CI_PATH),
        )

    def test_every_pip_install_of_uv_requires_hashes(self):
        for name, text in self.workflows.items():
            for line in text.splitlines():
                if "uv-requirements.txt" in line and "pip install" in line:
                    with self.subTest(workflow=name, line=line.strip()):
                        self.assertIn("--require-hashes", line)

    def test_uv_lock_is_committed(self):
        self.assertTrue((REPO_ROOT / "uv.lock").is_file())


class RepositoryPolicyParserFixtureTests(unittest.TestCase):
    """Fixture self-tests over crafted YAML strings, never the real
    workflow files."""

    def test_unapproved_action_is_reported(self):
        text = (
            "jobs:\n  build:\n    steps:\n      - uses: some/unapproved-action@"
            + "a" * 40
            + " # v1.0.0\n"
        )
        entries = parse_uses_lines(text)
        self.assertEqual(["some/unapproved-action"], find_unapproved_actions(entries))

    def test_tag_pinned_action_is_reported_as_unpinned(self):
        text = "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"
        entries = parse_uses_lines(text)
        self.assertEqual(["actions/checkout@v4"], find_unpinned_actions(entries))

    def test_local_reusable_workflow_needs_no_pin(self):
        text = "jobs:\n  ci:\n    uses: ./.github/workflows/ci.yml\n"
        entries = parse_uses_lines(text)
        self.assertEqual([], find_unapproved_actions(entries))
        self.assertEqual([], find_unpinned_actions(entries))

    def test_a_second_pin_for_the_same_action_is_reported(self):
        text = (
            "      - uses: actions/checkout@" + "a" * 40 + " # v7.0.1\n"
            "      - uses: actions/checkout@" + "b" * 40 + " # v7.0.2\n"
            "      - uses: actions/setup-python@" + "c" * 40 + " # v7.0.0\n"
            "      - uses: actions/setup-python@" + "c" * 40 + " # v7.0.0\n"
        )
        self.assertEqual(
            ["actions/checkout"], find_inconsistent_pins(parse_uses_lines(text))
        )

    def test_job_missing_permissions_is_reported(self):
        text = (
            "jobs:\n  build:\n    runs-on: ubuntu-24.04\n"
            "  publish:\n    permissions:\n      id-token: write\n"
        )
        self.assertEqual(["build"], find_jobs_missing_permissions(text))

    def test_id_token_write_job_is_found(self):
        text = (
            "jobs:\n  build:\n    permissions:\n      contents: read\n"
            "  publish:\n    permissions:\n      id-token: write\n"
        )
        self.assertEqual(["publish"], find_jobs_with_id_token_write(text))

    def test_every_write_scope_and_write_all_are_found(self):
        text = (
            "jobs:\n  build:\n    permissions:\n      contents: read\n"
            "  tag:\n    permissions:\n      contents: write\n      packages: write\n"
            "  wide:\n    permissions: write-all\n"
        )
        self.assertEqual(
            {"tag": ["contents", "packages"], "wide": ["write-all"]},
            find_write_permissions(text),
        )

    def test_checkout_credentials_are_read_per_step(self):
        sha = "a" * 40
        text = (
            "jobs:\n  a:\n    steps:\n"
            f"      - uses: actions/checkout@{sha} # v7.0.1\n"
            "        with:\n          persist-credentials: false\n"
            "      - run: echo hi\n"
            "  b:\n    steps:\n"
            f"      - uses: actions/checkout@{sha} # v7.0.1\n"
            f"      - uses: actions/setup-python@{sha} # v7.0.0\n"
            "        with:\n          persist-credentials: false\n"
        )
        self.assertEqual({"a": ["false"], "b": [None]}, checkout_credentials(text))

    def test_step_blocks_split_a_job_into_its_steps(self):
        job = (
            "  build:\n    needs: ci\n    steps:\n"
            "      - uses: a/b@x\n        with:\n          k: v\n"
            "      - name: Run\n        run: echo hi\n"
        )
        self.assertEqual(
            [
                "      - uses: a/b@x\n        with:\n          k: v",
                "      - name: Run\n        run: echo hi",
            ],
            step_blocks(job),
        )

    def test_expression_inside_single_line_run_is_reported(self):
        text = "steps:\n  - run: echo ${{ github.event.head_commit.message }}\n"
        self.assertEqual([2], find_run_expression_violations(text))

    def test_expression_inside_block_run_is_reported(self):
        text = "steps:\n  - run: |\n      echo ${{ matrix.python }}\n"
        self.assertEqual([3], find_run_expression_violations(text))

    def test_expression_in_with_block_is_not_a_run_violation(self):
        text = (
            "steps:\n  - uses: actions/setup-python@"
            + "b" * 40
            + "\n    with:\n      python-version: ${{ matrix.python }}\n"
        )
        self.assertEqual([], find_run_expression_violations(text))

    def test_plain_run_value_with_colon_space_is_a_hazard(self):
        text = "steps:\n  - run: python -m pip install --only-binary=:all: --no-deps\n"
        self.assertEqual([2], find_plain_run_scalar_hazards(text))

    def test_plain_run_value_with_space_hash_is_a_hazard(self):
        text = "steps:\n  - run: uv build # builds the wheel\n"
        self.assertEqual([2], find_plain_run_scalar_hazards(text))

    def test_plain_run_value_ending_with_a_colon_is_a_hazard(self):
        text = "steps:\n  - run: echo label:\n"
        self.assertEqual([2], find_plain_run_scalar_hazards(text))

    def test_plain_run_value_starting_with_an_indicator_is_a_hazard(self):
        for value in ("*alias", "&anchor x", "!tag x", "[a, b]", "{a: b}", "%x", "@x"):
            with self.subTest(value=value):
                text = f"steps:\n  - run: {value}\n"
                self.assertEqual([2], find_plain_run_scalar_hazards(text))

    def test_empty_or_unclosed_quoted_run_value_is_a_hazard(self):
        for value in ("", '"python -m pip install', "'uv build"):
            with self.subTest(value=value):
                text = f"steps:\n  - run: {value}\n"
                self.assertEqual([2], find_plain_run_scalar_hazards(text))

    def test_quoted_and_block_run_values_are_not_hazards(self):
        text = (
            "steps:\n"
            '  - run: "python -m pip install --only-binary=:all: --no-deps"\n'
            "  - run: 'uv build # literal'\n"
            "  - run: |\n"
            "      echo a: b # literal shell text\n"
            "  - run: >-\n"
            "      echo c: d\n"
            "  - run: uv run python -m unittest discover\n"
        )
        self.assertEqual([], find_plain_run_scalar_hazards(text))


if __name__ == "__main__":
    unittest.main()
