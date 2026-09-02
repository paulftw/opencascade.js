// Simulates N independent, fully-isolated "requests" against a
// hypothetical stateless cut-two-cylinders-and-reply server: each request
// builds its own two cylinders from scratch, cuts them, reads the result
// the way a real handler would, then destroys EVERYTHING it created --
// inputs included -- and forces mimalloc to actually purge before the
// next request starts. Nothing survives across requests on purpose,
// unlike cylinder-cut-pure-occt.cpp, which deliberately reuses the same
// two input shapes for all 40,000 cuts.
//
// Question: does committed memory return to the same floor after each
// request's own aggressive teardown+purge, or does every request leave a
// permanent, non-purgeable residue that would eventually OOM a
// long-running stateless server? No HTTP, no framing -- that's not the
// variable in question.

#include <cstdio>
#include <cstdlib>
#include <mimalloc.h>
#include <mimalloc-stats.h>

#include <BRepAlgoAPI_Cut.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <TopoDS_Shape.hxx>

namespace {

long long committedBytes() {
  mi_stats_t stats;
  mi_stats_init(&stats);
  mi_stats_get(&stats);
  return stats.committed.current;
}

// One fully self-contained request: build two cylinders, cut them, read
// the result the way a real handler would have to (so nothing here is
// dead-code-eliminated), then destroy every object created here before
// returning -- nothing escapes this function.
void handleRequest(double r1, double r2) {
  BRepPrimAPI_MakeCylinder mk1(r1, 20);
  TopoDS_Shape first = mk1.Shape();
  BRepPrimAPI_MakeCylinder mk2(r2, 20);
  TopoDS_Shape second = mk2.Shape();

  BRepAlgoAPI_Cut cut(first, second);
  cut.Build();
  TopoDS_Shape diff = cut.Shape();
  if (diff.IsNull()) {
    std::printf("{\"error\":\"cut produced a null shape\"}\n");
    std::exit(1);
  }
  volatile bool isNull = diff.IsNull();
  (void)isNull;
}  // mk1/first/mk2/second/cut/diff all destroyed here -- end of scope.

}  // namespace

int main(int argc, char** argv) {
  int requests = argc > 1 ? std::atoi(argv[1]) : 50;

  // Warm-up: absorb one-time lazy-init cost before baseline (same
  // reasoning as cylinder-cut-pure-occt.cpp).
  handleRequest(10, 9);
  mi_collect(true);
  long long baseline = committedBytes();
  std::printf(
      "{\"requests\":%d,\"mode\":\"stateless request sim, full teardown+purge per request\"}\n",
      requests);
  std::printf("{\"label\":\"baseline\",\"committedBytes\":%lld}\n", baseline);
  std::fflush(stdout);

  // Every request still gets full teardown+purge+measure regardless of
  // whether it's printed -- only stdout volume is thinned. Print every
  // request up to 100 (matches the earlier 50-request run's resolution),
  // periodic checkpoints after that, and -- unconditionally, regardless of
  // stride -- the exact request where committedBytes changes from the
  // previous request, so a late-onset shift can't hide between checkpoints.
  long long lastCommitted = baseline;
  for (int i = 1; i <= requests; ++i) {
    handleRequest(10, 9);
    mi_collect(true);
    long long committed = committedBytes();
    bool changed = committed != lastCommitted;
    bool checkpoint = i <= 100 || i == requests || i % 500 == 0;
    if (changed || checkpoint) {
      std::printf(
          "{\"label\":\"request-%d\",\"committedBytes\":%lld,\"deltaFromBaselineBytes\":%lld,\"deltaFromPrevBytes\":%lld%s}\n",
          i, committed, committed - baseline, committed - lastCommitted,
          changed ? ",\"changed\":true" : "");
      std::fflush(stdout);
    }
    lastCommitted = committed;
  }
  return 0;
}
