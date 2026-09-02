#!/usr/bin/env bash
# ASan/LeakSanitizer variant of build-cylinder-cut-pure-occt.sh: same source
# (compiled with -DOCJS_ASAN_BUILD so it skips the mimalloc stats calls --
# ASan's own malloc/new/delete replace mimalloc's for this build), linked
# with -fsanitize=address against the SAME uninstrumented OCCT static
# libraries. Only main.o needs the instrumentation flag: ASan's malloc/free/
# operator new/operator delete win the final symbol resolution regardless of
# which .o calls them, so LeakSanitizer's exit-time reachability scan still
# covers every allocation OCCT itself makes, without rebuilding OCCT.
#
# Usage: build-cylinder-cut-pure-occt-asan.sh [perRound] [rounds]
# perRound/rounds are baked into the invocation via argv at run time, not
# compile time -- run the produced .js directly with those as node args.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

: "${EMSDK:?set EMSDK to your emsdk checkout, e.g. /Users/paul/Projects/Cloudray/emsdk}"
source "$EMSDK/emsdk_env.sh" >/dev/null

OCJS_ROOT="$SCRIPT_DIR"
RAPIDJSON_ROOT="${RAPIDJSON_ROOT:-$SCRIPT_DIR/deps/rapidjson}"
FREETYPE_ROOT="${FREETYPE_ROOT:-$SCRIPT_DIR/deps/freetype}"
BUILD_DIR="$SCRIPT_DIR/build"
OUT_DIR="${OUT_DIR:-/private/tmp/claude-501/-Users-paul-Projects-Cloudray-opencascade-js/d890ae71-7ca4-46b8-baae-c58c924c6186/scratchpad}"

mkdir -p "$OUT_DIR"
OBJ="$OUT_DIR/cylinder-cut-pure-occt-asan.o"
OUT_JS="$OUT_DIR/cylinder-cut-pure-occt-asan.js"

echo "== compiling (asan) =="
emcc \
  -std=c++17 \
  -fsanitize=address \
  -DOCJS_ASAN_BUILD=1 \
  -sSUPPORT_LONGJMP=0 -sDISABLE_EXCEPTION_CATCHING=1 \
  -DIGNORE_NO_ATOMICS=1 \
  -DOCCT_NO_PLUGINS \
  -frtti \
  -DHAVE_RAPIDJSON \
  -O1 -g \
  -I"$BUILD_DIR/occt-includes" \
  -I"$RAPIDJSON_ROOT/include" \
  -I"$FREETYPE_ROOT/include/freetype" \
  -I"$FREETYPE_ROOT/include" \
  -I"$OCJS_ROOT/src" \
  -c "$SCRIPT_DIR/experiments/cylinder-cut-pure-occt.cpp" \
  -o "$OBJ"

echo "== linking (asan) =="
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
  -fsanitize=address \
  -fwasm-exceptions \
  -sALLOW_MEMORY_GROWTH=1 \
  -sINITIAL_MEMORY=256MB \
  -sMAXIMUM_MEMORY=4GB \
  -sUSE_FREETYPE=1 \
  -sSTACK_SIZE=8388608 \
  -sWASM_BIGINT \
  -O1 -g

echo "== built: $OUT_JS =="
