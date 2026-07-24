#!/usr/bin/env bash
# Reproduce the vlayer provenance build from a clean checkout — no `vlayer init`, no dead install
# domain, no S3 (all deps come from the soldeer registry + vlayer/risc0 GitHub release zips).
#
#   ./setup.sh          # installs deps + forge build
#   ./setup.sh test     # also runs forge test (needs testdata/fng_webproof.json — see README)
#
# Requires: forge (foundry). Run from the vlayer/ directory.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> forge soldeer install"
forge soldeer install

# risc0-ethereum-3.0.0 imports openzeppelin via its OWN (context-local) remappings but the release
# zip ships no dependencies/ — link our top-level @openzeppelin-contracts-5.1.0 into the path it expects.
echo "==> wiring risc0-ethereum's openzeppelin dependency"
mkdir -p dependencies/risc0-ethereum-3.0.0/dependencies
ln -sfn ../../@openzeppelin-contracts-5.1.0 \
  dependencies/risc0-ethereum-3.0.0/dependencies/@openzeppelin-contracts-5.1.0

echo "==> forge build"
forge build

if [ "${1:-}" = "test" ]; then
  echo "==> forge test"
  forge test
fi
echo "==> done"
