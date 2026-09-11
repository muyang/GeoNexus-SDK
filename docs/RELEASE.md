# Release process (v1.0)

Checklist for cutting a GeoNexus release. Everything is automated except the
PyPI upload (needs credentials) and the tag.

## Pre-release checklist

1. **Tests & quality**
   - [ ] `pytest` green (unit + integration + conformance)
   - [ ] `ruff check src tests examples` and `ruff format --check` clean
   - [ ] `mypy src/geonexus` clean
   - [ ] `coverage run -m pytest && coverage report` ≥ 80%
2. **Conformance** — `tests/conformance/` suites pass (GeoCard vectors,
   GeoMCP vectors); no vector may be changed without a spec discussion.
3. **Versions agree** — `geonexus.__version__`,
   `schemas/geocard.schema.json` (enum), `GeoMCP PROTOCOL_VERSION`,
   `pyproject.toml [project].version` all match the release version.
4. **Docs** — `CHANGELOG.md` updated; spec docs (`docs/GEOCARD.md`,
   `docs/GEOMCP.md`) match the frozen behaviour; `docs/API_STABILITY.md`
   reflects any API changes.
5. **Build** — `python -m build` produces sdist + wheel; the wheel installs
   and `import geonexus; geonexus.__version__` matches.

## Cut the release

```bash
# 1. Version bump (edit pyproject.toml + src/geonexus/__init__.py),
#    then from the repo root:
python -m build
pip install --force-reinstall --no-deps dist/*.whl
pytest                                    # against the built wheel env

# 2. Tag and push
git add -A && git commit -m "Release v1.0.0"
git tag v1.0.0
git push origin main --tags

# 3. Publish (requires PyPI credentials; skipped otherwise)
bash scripts/publish.sh --no-upload      # build-only check
bash scripts/publish.sh --test           # rehearsal on Test PyPI
bash scripts/publish.sh                   # uploads sdist+wheel to PyPI
```

> **Distribution name:** the PyPI project is `geonexus-sdk` (the bare
> `geonexus` name is occupied by an unrelated package); the import package
> stays `geonexus`. Users install with `pip install geonexus-sdk` and
> `import geonexus`.

> **Test PyPI note:** Test PyPI hosts an old broken `fastapi` sdist; when
> rehearsing an install from Test PyPI, install dependencies from the
> official index and the package itself with `--no-deps`:
> `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ geonexus==1.0.0`
> (or install deps first, then `--no-deps` from Test PyPI).

## Post-release

- [ ] Bump the version to the next dev version (e.g. `1.0.1.dev0`).
- [ ] Add a `[Unreleased]` section to `CHANGELOG.md`.

## Policy notes

- Breaking changes require a MAJOR version; deprecated names are removed no
  earlier than the next MAJOR (see `docs/API_STABILITY.md`).
- GeoCard `geocard_version` accepts `0.1` and `1.0`; the SDK writes `1.0`.
- GeoMCP methods and error codes are frozen for the 1.x line; new features
  are negotiated via `geo.capabilities`.
