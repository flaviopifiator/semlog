# Releasing semlog

Every merge to `main` tags the version declared in `pyproject.toml`, unless that tag already exists. Publishing that version to PyPI then waits for a maintainer to approve the protected `pypi` environment. The version itself is decided by the Conventional Commits since the last release tag, and a pull request check refuses any version or CHANGELOG.md that does not match them.

Normative rules: STANDARDS.md PRV-001 to PRV-006. The decision logic lives in `tools/release_policy.py` (standard library only) and is tested in `tests/test_release_policy.py`.

## Quick path

1. Write every commit in the pull request as a Conventional Commit.
2. If the commits since the last release tag require a release, set `version` in `pyproject.toml` to the released version bumped by exactly that level, and add a `## [X.Y.Z] - YYYY-MM-DD` section to CHANGELOG.md.
3. Merge. The `tag` workflow runs CI, builds the distributions, and pushes the annotated tag `vX.Y.Z`.
4. Approve the `pypi` deployment in that workflow run. The package is published with PEP 740 attestations.

## How the version is decided

The baseline is the last release, not the pull request's base. The check takes the highest `vMAJOR.MINOR.PATCH` tag and reads every commit since the last release tag up to the pull request head, including commits that earlier pull requests merged without being released yet.

| Commits since the last release tag | Required release |
|---|---|
| any `feat` | minor |
| `fix` or `perf` (and no `feat`) | patch |
| `!` after the type or scope, or a `BREAKING CHANGE:` footer (`BREAKING-CHANGE:` also counts) | major; minor while the major version is 0 (SemVer section 4) |
| only `docs`, `test`, `ci`, `chore`, `refactor`, `build`, `style` | none |
| merge commits (subject starts with `Merge `) | ignored |

The highest required level wins, and the expected version follows:

| Situation | Expected `pyproject.toml` version |
|---|---|
| a release tag `vA.B.C` exists and a release is required | `A.B.C` bumped by exactly that level |
| a release tag exists and nothing requires a release | `A.B.C`, unchanged |
| no release tag exists yet (first release) | the version `main` already declares, unchanged; commits decide no bump |

Before the first release the check still validates the pull request's own commits, but not the whole history: that history predates this policy, and its first commit ("Initial commit") is not a Conventional Commit.

The policy refuses to guess:

- A subject that is not `type(optional scope)!: description` is an error, not a silent "no release".
- A type outside the list above (for example `revert`) is an error.
- A `v*` tag that is not `vMAJOR.MINOR.PATCH` is an error, because the check cannot tell which release it marks.
- Versions must be plain `MAJOR.MINOR.PATCH`. Pre-release and build metadata are not supported.
- Moving from `0.y.z` to `1.0.0` is not something commits can express: a breaking change at major version 0 requires a minor bump, so the check rejects `1.0.0`. That step is a maintainer decision (see [Manual release](#manual-release)).

## What the pull request check enforces

The `version-check` workflow (`.github/workflows/version-check.yml`) runs on every pull request to `main`, with `contents: read` only. Its full-history checkout (`fetch-depth: 0`) also fetches every tag. It runs `python tools/release_policy.py latest-tag` on `git tag --list 'v*'`, reads the commits since that tag (or the pull request's own commits before the first release) and `pyproject.toml` at the base and head, then runs `python tools/release_policy.py check-pr`. It fails when:

- any commit subject is not a valid Conventional Commit, or a `v*` tag is malformed;
- the head version is not the expected version from the table above;
- the head version is not released yet and CHANGELOG.md has no non-empty `## [X.Y.Z] - YYYY-MM-DD` section for it. This includes the first release.

Run the same check locally (Python 3.11 or newer, because it reads TOML with `tomllib`):

```bash
git fetch origin main --tags
out="$(mktemp -d)"
git tag --list 'v*' > "$out/tags.txt"
released="$(python3 tools/release_policy.py latest-tag --tags "$out/tags.txt")"
if [ -n "$released" ]; then range="refs/tags/$released..HEAD"; else range="origin/main..HEAD"; fi
git log --format=%B%x00 "$range" > "$out/commits.txt"
git show origin/main:pyproject.toml > "$out/base-pyproject.toml"
python3 tools/release_policy.py check-pr --tags "$out/tags.txt" --commits "$out/commits.txt" --base-pyproject "$out/base-pyproject.toml"
```

## What happens on merge

The `tag` workflow (`.github/workflows/tag.yml`) runs on every push to `main`:

| Job | Permissions | What it does |
|---|---|---|
| `plan` | `contents: read` | Runs `python tools/release_policy.py tag-info` to read the version and its CHANGELOG.md section. If the tag `vX.Y.Z` already exists, it logs "already exists; nothing to release" and every later job is skipped. |
| `ci` | `contents: read` | Runs the full `ci.yml` suite on the merged commit. |
| `build` | `contents: read` | Runs `python tools/release_policy.py verify-tag`, then `uv build`, and uploads the distributions. |
| `tag` | `contents: write` | Checks the remote again with `git ls-remote --tags origin`. If the tag exists, it exits successfully without changes. Otherwise it creates the annotated tag, with the CHANGELOG.md section as its message, as `github-actions[bot]`, and pushes it. It runs no repository code, and it is the only checkout that keeps credentials. |
| `publish` | `id-token: write` | Runs only if this run created the tag and the repository is public. Waits for approval of the `pypi` environment, then publishes through Trusted Publishing. |

A tag is therefore created only for a commit that passed CI and built cleanly.

### Why `tag.yml` publishes by itself

- A tag pushed with the default `GITHUB_TOKEN` does not start `release.yml`. GitHub: "events triggered by the `GITHUB_TOKEN` will not create a new workflow run" ([GITHUB_TOKEN docs](https://docs.github.com/en/actions/concepts/security/github_token)).
- `release.yml` cannot be called as a reusable workflow to publish either. PyPI: "Reusable workflows cannot currently be used as the workflow in a Trusted Publisher" ([PyPI troubleshooting](https://docs.pypi.org/trusted-publishers/troubleshooting/#reusable-workflows-on-github)).

So `tag.yml` and `release.yml` each contain the same `build` and `publish` steps. `tests/test_repository_policy.py` fails if those steps drift apart.

## Approving a release

1. Open the `tag` workflow run for the merge (Actions tab). It shows "Waiting" on `publish`.
2. Click **Review deployments**, select `pypi`, and approve.
3. Check the release at https://pypi.org/p/semlog.

If you reject the deployment, or the upload fails, the tag stays and nothing is published. Use **Re-run failed jobs** in the same run (GitHub allows re-runs up to 30 days after the initial run), or [publish the existing tag](#publish-an-existing-tag).

## One-time maintainer setup

### 1. Keep the repository public

Both `publish` jobs are skipped while the repository is private. On GitHub Free, Pro, and Team plans, required reviewers are only available for public repositories.

While the repository is private, merges still create tags, but those versions are never published automatically. After making the repository public, [publish the existing tag](#publish-an-existing-tag).

### 2. Create the `pypi` environment

In **Settings > Environments**, create `pypi` and enable **Required reviewers** with the maintainers who may approve releases. Leave **Prevent self-review** off unless a second maintainer can approve: the person who merges is the one who starts the deployment.

If you add deployment branch and tag rules, allow both the `main` branch (used by `tag.yml` and by manual runs of `release.yml`) and tags matching `v*` (used by `release.yml` on a tag push).

### 3. Register the trusted publishers on PyPI

PyPI checks the workflow filename and the environment of the job that publishes. Each publishing workflow needs its own registration:

| PyPI field | `tag.yml` publisher | `release.yml` publisher |
|---|---|---|
| PyPI Project Name | `semlog` | `semlog` |
| Owner | `flaviopifiator` | `flaviopifiator` |
| Repository name | `semlog` | `semlog` |
| Workflow name | `tag.yml` | `release.yml` |
| Environment name | `pypi` | `pypi` |

semlog already exists on PyPI, so register both publishers on the project's own **Publishing** page. A "pending" publisher (**Your account > Publishing**) is only for a project that does not exist yet, and it does not reserve the name until it is used.

### 4. Protect `main`

- Require the `version-check` and `ci` status checks.
- Require branches to be up to date before merging, and let the previous merge's `tag` run create its tag before merging the next pull request that changes the version. Otherwise two pull requests can both claim the same next version, and the second merge is silently skipped because its tag already exists.
- Prefer merge commits. The check validates each commit in the pull request, not a squash commit message written at merge time.

If you add a tag ruleset for `v*`, make sure it still lets this workflow's `GITHUB_TOKEN` create tags, or the `tag` job fails.

## Manual release

Use this only when the automatic path cannot work: for example, a version tagged while the repository was private, a run older than 30 days, or the move to `1.0.0`.

### Publish an existing tag

When the tag already exists but the version never reached PyPI:

1. In the Actions tab, open the `release` workflow and click **Run workflow**. Keep **Use workflow from** on `main`, enter the tag (for example `v0.1.0`), and run it. From a terminal: `gh workflow run release.yml -f tag=v0.1.0`.
2. The run (`workflow_dispatch`) tests and builds the tag's commit, not `main`: CI runs on the tag, `python tools/release_policy.py verify-tag` checks the tag against its `pyproject.toml` and CHANGELOG.md section, and the distributions are built from it.
3. Approve the `pypi` deployment. The repository must be public, and the `release.yml` trusted publisher must be registered.

Never try to publish a version that PyPI already accepted: PyPI never allows a filename to be reused, even after deletion.

### Create a missing tag

When no tag exists for a version on `main`:

1. Make sure the target commit on `main` declares the version in `pyproject.toml` and has its CHANGELOG.md section.
2. Create and push the tag yourself:

   ```bash
   git tag --annotate vX.Y.Z <commit>
   git push origin vX.Y.Z
   ```

3. A tag pushed by a person starts `release.yml`, which runs CI, checks the tag, builds, and waits for `pypi` approval.

To move to `1.0.0`, merge that version change with a maintainer override of the `version-check` failure. The merge tags it; publish it by approving that run, or through [Publish an existing tag](#publish-an-existing-tag).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `version-check`: "not a Conventional Commit" | Reword the listed commits (interactive rebase) and force-push the branch. |
| `version-check`: "the version must be X" | Set `pyproject.toml` to the expected version shown in the message. |
| `version-check`: "CHANGELOG.md has no section" | Add `## [X.Y.Z] - YYYY-MM-DD` with the release notes. |
| `tag` / `plan`: "CHANGELOG.md has no section" | The version on `main` has no changelog section. Merge a `docs:` pull request that adds it. |
| `publish`: `invalid-publisher` | The PyPI registration does not match: check the workflow name and the `pypi` environment in the table above. |

## Policy tool reference

| Command | Used by | Inputs |
|---|---|---|
| `python tools/release_policy.py latest-tag` | `version-check.yml` | `--tags` (one tag per line); prints the latest `vMAJOR.MINOR.PATCH` tag, or nothing before the first release |
| `python tools/release_policy.py check-pr` | `version-check.yml` | `--tags`, `--commits` (NUL-separated messages since the latest release tag), `--base-pyproject`, `--head-pyproject`, `--changelog` |
| `python tools/release_policy.py tag-info` | `tag.yml` `plan` | `--pyproject`, `--changelog`; prints `version`, `tag`, and `message` as step outputs |
| `python tools/release_policy.py verify-tag` | `build` in both publishing workflows | `RELEASE_TAG` environment variable (the dispatched tag, the pushed tag, or the tag `tag.yml` is about to create); `--pyproject`, `--changelog` |
