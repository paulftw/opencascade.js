import init from '../dist/opencascade_single.js';
import { fr } from '../src/runtime/fr.ts';

const iterations = Number(process.argv[2] ?? 100_000);
const batchSize = Number(process.argv[3] ?? 10_000);
const rounds = Number(process.argv[4] ?? 5);
const pauseMs = Number(process.argv[5] ?? 10_000);

if (![iterations, batchSize, rounds, pauseMs].every(Number.isInteger) || iterations <= 0 || batchSize <= 0 || rounds <= 0 || pauseMs < 0) {
  throw new Error('Usage: node --expose-gc experiments/make-cylinder-memory-leak.mjs [iterations] [batchSize] [rounds] [pauseMs]');
}
if (typeof global.gc !== 'function') {
  throw new Error('Run Node with --expose-gc');
}

// This run deliberately never calls .delete() — every cylinder is
// constructed via fr() and then dropped, relying entirely on fr()'s
// FinalizationRegistry backstop to reclaim it once GC collects the proxy.
// The point of the experiment is to find out whether that backstop alone
// (no explicit disposal at all) keeps WASM memory bounded at scale, not to
// demonstrate the happy path of disciplined manual cleanup.

let asyncFailure = null;
process.on('uncaughtException', (error) => {
  // A FinalizationRegistry callback that throws is reported here, not at
  // any catchable call site — without this handler such an exception would
  // print a default Node warning and the run would silently keep going.
  asyncFailure = error;
  console.error('[FAIL] uncaughtException:', error);
});
process.on('unhandledRejection', (reason) => {
  asyncFailure = reason instanceof Error ? reason : new Error(String(reason));
  console.error('[FAIL] unhandledRejection:', reason);
});

const oc = await init({
  locateFile: (filename) => new URL(`../dist/${filename}`, import.meta.url).pathname,
});

let leakedCount = 0;
const realWarn = console.warn.bind(console);
console.warn = (...args) => {
  // fr()'s backstop is expected to fire for essentially every instance here
  // (nothing is ever explicitly deleted) — counting instead of printing
  // avoids drowning the memory signal in per-instance stack traces, without
  // discarding anything: an unexpected warning still prints in full.
  if (typeof args[0] === 'string' && args[0].startsWith('[ocjs] auto-disposed leaked')) {
    leakedCount += 1;
    return;
  }
  realWarn(...args);
};

function snapshot(label) {
  global.gc();
  const memory = process.memoryUsage();
  const wasmBytes = oc.wasmMemory?.buffer.byteLength ?? 0;
  console.log(JSON.stringify({
    label,
    rssMiB: +(memory.rss / 2 ** 20).toFixed(1),
    wasmMiB: +(wasmBytes / 2 ** 20).toFixed(1),
    externalMiB: +(memory.external / 2 ** 20).toFixed(1),
    arrayBuffersMiB: +(memory.arrayBuffers / 2 ** 20).toFixed(1),
    leakedCount,
  }));
}

console.log(JSON.stringify({ iterations, batchSize, rounds, pauseMs }));
snapshot('baseline');

let completedTotal = 0;
const gcTimer = setInterval(() => global.gc(), 5_000);
try {
  for (let round = 1; round <= rounds; round += 1) {
    for (let completed = 0; completed < iterations; completed += batchSize) {
      const count = Math.min(batchSize, iterations - completed);
      for (let index = 0; index < count; index += 1) {
        const builder = fr(new oc.BRepPrimAPI_MakeCylinder(10, 20));
        void builder; // no .delete() — the backstop is the only thing reclaiming this
        completedTotal += 1;
      }
    }
    snapshot(`after-round-${round}`);
    if (round < rounds && pauseMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, pauseMs));
      snapshot(`after-pause-${round}`);
    }
  }
} finally {
  clearInterval(gcTimer);
}

// Final flush: give the backstop every reasonable chance to catch up before
// judging whether anything is genuinely stuck, rather than just not yet
// collected under normal GC scheduling.
for (let i = 0; i < 10; i += 1) {
  await new Promise((resolve) => setTimeout(resolve, 200));
  global.gc();
}

// A single flat loop has one loop-tail: the very last `builder` binding can
// outlive the synchronous script end in V8 (no subsequent iteration to
// prove it dead), independent of `iterations`/`rounds` — confirmed on the
// nested cylinder-cut-tessellation experiment, where the residual was
// always exactly "last iteration's locals per loop nesting level," never
// scaling with total allocations. 4 is a generous fixed bound for this
// single-loop case, not a per-item tolerance.
const unreclaimedTolerance = 4;
const unreclaimed = completedTotal - leakedCount;
snapshot('final');
console.log(JSON.stringify({ completedTotal, leakedCount, unreclaimed, fullyReclaimed: unreclaimed === 0 }));

if (asyncFailure) {
  console.error('[FAIL] an exception was thrown asynchronously during the run — see above');
  process.exitCode = 1;
} else if (unreclaimed > unreclaimedTolerance) {
  console.error(`[FAIL] ${unreclaimed} instance(s) were never reclaimed by the backstop — exceeds the loop-tail tolerance of ${unreclaimedTolerance}`);
  process.exitCode = 1;
} else {
  console.log(`[PASS] ${completedTotal - unreclaimed}/${completedTotal} instances were reclaimed by the backstop (${unreclaimed} loop-tail residual, within tolerance); no uncaught exceptions`);
}
