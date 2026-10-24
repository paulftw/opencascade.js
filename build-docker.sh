#!/usr/bin/env bash
# Builds the wasm variants from an image built out of this checkout, then
# assembles dist/ and dist-memsafety/, which the libcascade package exports.
# CONTAINER_ENGINE=podman selects podman; the default is docker.
# LIBCASCADE_VARIANTS selects the variants; assemble needs every variant that
# libcascade.config.ts declares.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="${CONTAINER_ENGINE:-docker}"
VARIANTS="${LIBCASCADE_VARIANTS:-single}"
cd "$REPO_DIR"

npm ci --ignore-scripts --no-audit --no-fund
npm run build:toolchain

for variant in $VARIANTS; do
  case "$variant" in
    single) target=final-single ;;
    multi) target=final-multi ;;
    *) echo "Unknown variant: $variant" >&2; exit 2 ;;
  esac
  image="libcascade-$variant:local"
  "$ENGINE" build \
    --target "$target" \
    --tag "$image" \
    --build-arg "REVISION=$(git rev-parse HEAD)" \
    --build-arg "OCJS_SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)" \
    --build-arg "VERSION=$(node -p "require('./package.json').version")" \
    .
  LIBCASCADE_IMAGE="$image" LIBCASCADE_CONTAINER_CMD="$ENGINE" \
    node packages/toolchain/bin/libcascade.mjs build --variant "$variant"
done

node packages/toolchain/bin/libcascade.mjs assemble
npm run build:memsafety
