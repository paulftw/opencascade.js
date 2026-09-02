// Embind-free control for the cut-phase leak isolated in
// experiments/cylinder-cut-tessellation-memory.mjs (see also the scratchpad
// phase-isolation*.mjs experiments). Same op, same fixed inputs, same
// perRound/rounds -- but plain stack-allocated OCCT C++ objects with normal
// destructors, no embind, no JS<->WASM marshaling, no FinalizationRegistry.
//
// If wasmMiB still climbs here, the leak is inside OCCT's own C++ (BOPAlgo_*
// allocation pattern) and embind is not implicated. If it stays flat, the
// leak is specific to the embind/ocjs_bindgen usage layer.
//
// Compile (from repo root, with emsdk activated):
//   see experiments/build-cylinder-cut-pure-occt.sh
// Run:
//   node experiments/cylinder-cut-pure-occt.js

#include <cstdio>
#include <cstdlib>
#include <emscripten/heap.h>
#ifndef OCJS_ASAN_BUILD
#include <mimalloc.h>
#include <mimalloc-stats.h>
#endif

#include <BRepAlgoAPI_Cut.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <TopoDS_Shape.hxx>

namespace {

// wasmMiB (emscripten_get_heap_size, the WebAssembly.Memory byte length) is
// a coarse, 128MB-granular signal -- the original embind isolation
// (scratchpad/phase-isolation.mjs 'cut' phase) never moved it either; the
// ~2MiB/40k-cuts creep it caught only showed up in mimalloc's own
// committed-bytes stat (arenaMiB below), read the same way
// OCJS::getMallinfo() reads it in embind_builtins.py, just without embind.
// Under OCJS_ASAN_BUILD, mimalloc isn't linked in at all -- ASan owns
// malloc/new/delete instead, so this only reports wasmMiB.
void snapshot(const char* label) {
  double wasmMiB = static_cast<double>(emscripten_get_heap_size()) / (1024.0 * 1024.0);
#ifndef OCJS_ASAN_BUILD
  mi_stats_t stats;
  mi_stats_init(&stats);
  mi_stats_get(&stats);
  double arenaMiB = static_cast<double>(stats.committed.current) / (1024.0 * 1024.0);
  std::printf("{\"label\":\"%s\",\"wasmMiB\":%.2f,\"arenaMiB\":%.2f}\n", label, wasmMiB, arenaMiB);
#else
  std::printf("{\"label\":\"%s\",\"wasmMiB\":%.2f}\n", label, wasmMiB);
#endif
  std::fflush(stdout);
}

}  // namespace

int main(int argc, char** argv) {
  int kPerRound = argc > 1 ? std::atoi(argv[1]) : 2000;
  int kRounds = argc > 2 ? std::atoi(argv[2]) : 20;
  std::printf(
      "{\"perRound\":%d,\"rounds\":%d,\"mode\":\"pure C++, no embind\"}\n",
      kPerRound, kRounds);

  // Warm-up: one full create-cut-destroy cycle, thrown away, before
  // "baseline" is captured. Absorbs any one-time lazy-init cost (OCCT
  // static tables, first-touch pages, etc.) that would otherwise land in
  // round-1's delta and never recur -- baseline should reflect steady
  // state, not cold start, so the later teardown/purge comparison is fair.
  {
    BRepPrimAPI_MakeCylinder warm1(10, 20);
    TopoDS_Shape warmFirst = warm1.Shape();
    BRepPrimAPI_MakeCylinder warm2(9, 20);
    TopoDS_Shape warmSecond = warm2.Shape();
    BRepAlgoAPI_Cut warmCut(warmFirst, warmSecond);
    warmCut.Build();
    TopoDS_Shape warmDiff = warmCut.Shape();
    if (warmDiff.IsNull()) {
      std::printf("{\"error\":\"warm-up cut produced a null shape\"}\n");
      return 1;
    }
  }  // warm1/warmFirst/warm2/warmSecond/warmCut/warmDiff all destroyed here

  snapshot("baseline");

  {
    BRepPrimAPI_MakeCylinder mk1(10, 20);
    TopoDS_Shape first = mk1.Shape();
    BRepPrimAPI_MakeCylinder mk2(9, 20);
    TopoDS_Shape second = mk2.Shape();

    for (int round = 1; round <= kRounds; ++round) {
      for (int i = 0; i < kPerRound; ++i) {
        BRepAlgoAPI_Cut cut(first, second);
        cut.Build();
        TopoDS_Shape diff = cut.Shape();
        if (diff.IsNull()) {
          std::printf("{\"error\":\"cut produced a null shape\"}\n");
          return 1;
        }
        // `diff` and `cut` fall out of scope here; ~BRepAlgoAPI_Cut and
        // TopoDS_Shape's refcounted handle destructor run normally, exactly
        // as in any OCCT tutorial -- no explicit free() call of any kind.
      }
      char label[16];
      std::snprintf(label, sizeof(label), "round-%d", round);
      snapshot(label);
    }

    snapshot("after-loop");
  }  // mk1/first/mk2/second destroyed here -- every OCCT object this
     // program ever created (besides the already-gone warm-up ones) is
     // now gone.

  snapshot("after-teardown");

#ifndef OCJS_ASAN_BUILD
  // Force mimalloc to return any pages it's still holding but not using
  // back to a purged state, so "after-teardown" staying elevated can't be
  // blamed on mimalloc just being lazy about it.
  mi_collect(true);
#endif
  snapshot("after-purge");

  return 0;
}
