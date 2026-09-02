import init from '../../dist/opencascade_single.js';
import { fr } from '../../src/runtime/fr.js';

// Full cut + tessellation round trip, disposed entirely through fr()'s
// FinalizationRegistry backstop -- no explicit .delete() anywhere, matching
// a consumer who trusts the backstop rather than hand-writing delete()
// chains. Fresh cylinders built inside every op, never reused across ops --
// the one pattern already proven safe this session (see
// scratchpad/cylinder-cut-stateless-request-sim.cpp: O(1), flat to 50,000
// ops). This tests whether that safety holds through fr()'s real,
// GC-timing-dependent disposal path too, not just explicit synchronous
// delete -- and with the TopoDS.Face() Leaking<T> fix now in place
// (src/declarations/builtin-bindings.d.ts) so the tessellation phase's
// face actually gets tracked instead of silently skipped.
//
// wasmMiB (the real WebAssembly.Memory size) is the number that actually
// matters -- it's a one-way ratchet that only grows when the allocator
// genuinely needs more bytes than the buffer currently has. If it stays
// flat, nothing is leaking in the sense that matters for OOM risk.

const totalOps = Number(process.argv[2] ?? 5000);
const delay = Number(process.argv[3] ?? 500);

if (!Number.isInteger(totalOps) || totalOps < 1) {
  throw new Error('Usage: node --expose-gc experiments/cylinder-cut-tessellation-memory.mjs [totalOps] [delayMs]');
}
if (typeof globalThis.gc !== 'function') {
  throw new Error('Run Node with --expose-gc');
}

let asyncFailure = null;
process.on('uncaughtException', (error) => {
  // fr()'s FinalizationRegistry callback runs outside any try/catch this
  // script can see -- an exception inside it surfaces here, not at any
  // catchable call site.
  asyncFailure = error;
  console.error('[FAIL] uncaughtException:', error);
});
process.on('unhandledRejection', (reason) => {
  asyncFailure = reason instanceof Error ? reason : new Error(String(reason));
  console.error('[FAIL] unhandledRejection:', reason);
});

const oc = await init({
  locateFile: (filename) => new URL(`../../dist/${filename}`, import.meta.url).pathname,
});

function snapshot(label) {
  globalThis.gc();
  const wasmBytes = oc.wasmMemory?.buffer.byteLength ?? 0;
  const mallinfo = typeof oc.OCJS?.getMallinfo === 'function' ? oc.OCJS.getMallinfo() : null;
  console.log(JSON.stringify({
    label,
    wasmMiB: +(wasmBytes / 2 ** 20).toFixed(2),
    ...(mallinfo && { arenaMiB: +(mallinfo.arena / 2 ** 20).toFixed(2) }),
  }));
}

let b1, b2, first, second;

function foo() {
  b1 = fr(new oc.BRepPrimAPI_MakeCylinder(10, 20));
  first = fr(b1.Shape());
  b2 = fr(new oc.BRepPrimAPI_MakeCylinder(9, 20));
  second = fr(b2.Shape());
}

function clone(s) {
  const xerox = fr(new oc.BRepBuilderAPI_Copy(s));
  return fr(xerox.Shape());
}


function runOp() {
  const cut = fr(new oc.BRepAlgoAPI_Cut(first, second));
  cut.Build();
  const diff = fr(cut.Shape());
  if (diff.IsNull()) {
    throw new Error('cut produced a null shape');
  }

  const mesh = fr(new oc.BRepMesh_IncrementalMesh(diff, 0.5, false, 0.5, false));
  const explorer = fr(new oc.TopExp_Explorer(
    diff,
    oc.TopAbs_ShapeEnum.TopAbs_FACE,
    oc.TopAbs_ShapeEnum.TopAbs_SHAPE,
  ));
  if (!explorer.More()) {
    throw new Error('difference contains no faces');
  }
  const shape = fr(explorer.Current());
  const face = fr(oc.TopoDS.Face(shape));
  const location = fr(new oc.TopLoc_Location());
  const triangulation = fr(oc.BRep_Tool.Triangulation(face, location, 0));
  if (triangulation.NbTriangles() < 1 || triangulation.NbNodes() < 1) {
    throw new Error('bad triangulation');
  }

  const nbNodes = Math.min(1000, triangulation.NbNodes());
  for (let i = 1; i <= nbNodes; i++) {
    const node = fr(triangulation.Node(i), );
    void node.X();
    void node.Y();
    void node.Z();
  }

  const nbTriangles = Math.min(1000, triangulation.NbTriangles());
  for (let i = 1; i <= nbTriangles; i++) {
    const triangle = fr(triangulation.Triangle(i));
    void triangle.Value(1);
    void triangle.Value(2);
    void triangle.Value(3);
  }

  void mesh;
}

console.log(JSON.stringify({
  totalOps,
  delay,
  mode: 'fresh inputs per op, full cut+tessellation, fr()-backstop only, no explicit delete',
}));

foo();
runOp(); // warm-up -- absorbs one-time lazy-init cost before baseline
snapshot('baseline');

const N = Number(process.argv[4] ?? 100);
let completed = 0;

const intervalId = setInterval(() => {
  // clone second only leaks after 3k last args: 10 500
  // clone first only leaks after 3.5k last args: 10 500
  // clone both doesn't leak after 50k

  first = clone(first);
  second = clone(second);
  // foo();

  for (let i = 0; i < N; i++) {
    runOp();
    completed++;
  }
  snapshot(`op ${completed / 1000}k`);

  if (completed >= totalOps) {
    clearInterval(intervalId);

    // Final flush -- give the backstop every reasonable chance to catch up
    // before judging whether anything is genuinely stuck.
    let flushes = 0;
    const flushTimer = setInterval(() => {
      globalThis.gc();
      flushes++;
      if (flushes >= 10) {
        clearInterval(flushTimer);
        snapshot('final');
        if (asyncFailure) {
          console.error('[FAIL] an exception was thrown asynchronously during the run — see above');
          process.exitCode = 1;
        } else {
          console.log('[DONE]');
        }
      }
    }, 200);
  }
}, delay);
