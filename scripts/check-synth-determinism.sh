#!/usr/bin/env bash
# Regenerate all synthetic data from synth/cache (no model calls) and fail if anything
# differs from what is checked in. Gzip bytes can vary across zlib builds, so .gz files
# are compared through the uncompressed sha256 recorded in each manifest.
set -euo pipefail
cd "$(dirname "$0")/.."
SYNTH_OFFLINE=1 uv run python -m synth >/dev/null
git diff --exit-code -- data evals/datasets ':(exclude)*.gz'
echo "synthetic data is deterministic"
