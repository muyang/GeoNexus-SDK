#!/usr/bin/env bash
# Publish the GeoNexus SDK to PyPI (real or Test).
#
#   bash scripts/publish.sh                     # build + upload to PyPI
#   bash scripts/publish.sh --no-upload         # build + verify only
#   bash scripts/publish.sh --test              # build + upload to Test PyPI
#
# Credentials (PyPI API token): set TWINE_USERNAME=__token__ and
# TWINE_PASSWORD=<token>, or create ~/.pypirc. Without credentials the
# script builds and verifies, then exits 0 without uploading.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=${PYTHON:-python3}
fi

UPLOAD=1
REPOSITORY="pypi"
for arg in "$@"; do
  case "$arg" in
    --no-upload) UPLOAD=0 ;;
    --test) REPOSITORY="testpypi" ;;
  esac
done

echo "==> Building sdist + wheel"
"$PYTHON" -m build

echo "==> Verifying the wheel installs"
TMPVENV=$(mktemp -d)
"$PYTHON" -m venv "$TMPVENV/venv"
"$TMPVENV/venv/bin/pip" install --quiet dist/*.whl
"$TMPVENV/venv/bin/python" -c "import geonexus; print('installed version:', geonexus.__version__)"
rm -rf "$TMPVENV"

if [[ "$UPLOAD" == "1" ]]; then
  if [[ -z "${TWINE_USERNAME:-}" && -z "${TWINE_PASSWORD:-}" && ! -f ~/.pypirc ]]; then
    echo "==> No PyPI credentials found; skipping upload."
    echo "    Set TWINE_USERNAME=__token__ TWINE_PASSWORD=<token> (or create ~/.pypirc), then re-run."
    exit 0
  fi
  echo "==> Uploading to ${REPOSITORY}"
  "$PYTHON" -m twine upload --repository "$REPOSITORY" dist/*
else
  echo "==> Build-only check done (no upload)."
fi
