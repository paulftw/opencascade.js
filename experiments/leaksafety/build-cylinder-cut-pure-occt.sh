#!/usr/bin/env bash
# Compiles + links experiments/cylinder-cut-pure-occt.cpp directly against
# the cached OCCT static libraries in build/occt-libraries, with no embind
# and no ocjs_bindgen involvement at all -- see the comment at the top of
# the .cpp for why. Mirrors the compile/link flags ocjs_bindgen itself uses
# (src/ocjs_bindgen/config/flags.py, src/ocjs_bindgen/link/yaml_build.py,
# build-configs/full.yml) so the result is a fair comparison against the
# real build, just swapping the embind bindings TU for a plain main().
#
# Requires: a previously completed `./build-wasm.sh full build-configs/full.yml`
# (or any build that populated build/occt-libraries + build/occt-includes),
# and EMSDK pointing at an activated emsdk checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

: "${EMSDK:?set EMSDK to your emsdk checkout, e.g. /Users/paul/Projects/Cloudray/emsdk}"
source "$EMSDK/emsdk_env.sh" >/dev/null

OCJS_ROOT="$SCRIPT_DIR"
OCCT_ROOT="${OCCT_ROOT:-$SCRIPT_DIR/deps/OCCT}"
RAPIDJSON_ROOT="${RAPIDJSON_ROOT:-$SCRIPT_DIR/deps/rapidjson}"
FREETYPE_ROOT="${FREETYPE_ROOT:-$SCRIPT_DIR/deps/freetype}"
BUILD_DIR="$SCRIPT_DIR/build"
OUT_DIR="${OUT_DIR:-/private/tmp/claude-501/-Users-paul-Projects-Cloudray-opencascade-js/d890ae71-7ca4-46b8-baae-c58c924c6186/scratchpad}"

mkdir -p "$OUT_DIR"
OBJ="$OUT_DIR/cylinder-cut-pure-occt.o"
OUT_JS="$OUT_DIR/cylinder-cut-pure-occt.js"

echo "== compiling =="
emcc \
  -std=c++17 \
  -sSUPPORT_LONGJMP=0 -sDISABLE_EXCEPTION_CATCHING=1 \
  -DIGNORE_NO_ATOMICS=1 \
  -DOCCT_NO_PLUGINS \
  -frtti \
  -DHAVE_RAPIDJSON \
  -O3 \
  -I"$BUILD_DIR/occt-includes" \
  -I"$RAPIDJSON_ROOT/include" \
  -I"$FREETYPE_ROOT/include/freetype" \
  -I"$FREETYPE_ROOT/include" \
  -I"$OCJS_ROOT/src" \
  -c "$SCRIPT_DIR/experiments/cylinder-cut-pure-occt.cpp" \
  -o "$OBJ"

echo "== linking =="
LIBS=()
while IFS= read -r lib; do
  LIBS+=("$lib")
done < <(python3 -c "
import json
with open('$BUILD_DIR/occt-libraries/manifest.json') as f:
    m = json.load(f)
for entry in m['files']:
    print('$BUILD_DIR/occt-libraries/' + entry['path'])
")

emcc \
  "$OBJ" "${LIBS[@]}" \
  -o "$OUT_JS" \
  -fwasm-exceptions \
  -sALLOW_MEMORY_GROWTH=1 \
  -sINITIAL_MEMORY=128MB \
  -sMAXIMUM_MEMORY=4GB \
  -sUSE_FREETYPE=1 \
  -sSTACK_SIZE=8388608 \
  -sWASM_BIGINT \
  -sEVAL_CTORS=2 \
  -msimd128 \
  -sMALLOC=mimalloc \
  -O3

echo "== built: $OUT_JS =="
echo "$OUT_JS" > "$OUT_DIR/cylinder-cut-pure-occt.out-path"
