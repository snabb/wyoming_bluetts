# Contributing

## Development Setup

1. Install dependencies: `uv sync --all-extras --dev`
2. Make your changes.
3. Ensure pre-commit hooks pass: `prek run --all-files`
4. Ensure tests pass: `uv run -m pytest`
5. Submit a PR.

`uv.lock` is committed (this is an application, not a library) for
reproducible `uv sync`/`uv run`/CI installs. The `uv-lock` pre-commit hook
keeps it in sync with `pyproject.toml` automatically — just include it in
your commit if the hook modifies it. Note the Dockerfiles use `uv pip
install`, not `uv sync --frozen`, so they don't currently read this file;
it governs local dev and CI test runs.

## Releasing a New Version

The version must be updated in **three** places (keep them in sync):

1. `pyproject.toml` — `version = "X.Y.Z"`
2. `wyoming_bluetts/__init__.py` — `__version__ = "X.Y.Z"`
3. `config.yaml` — `version: X.Y.Z` (Home Assistant app version)

Then update `CHANGELOG.md` with the changes.

Commit and push to `master`. CI then builds and publishes
`ghcr.io/snabb/wyoming_bluetts:<version>` and `:<version>-cloning` (alongside
`latest`/`latest-cloning`) automatically — no manual tagging step needed for
the Docker images themselves.

The git tag and GitHub Release are **not** automated and must be created by
hand once CI is green:

```bash
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes "$(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '1d;$d')"
```

Without this, the version bump and image publish still work, but the repo's
"Latest release" on GitHub stays stuck on the previous version.
