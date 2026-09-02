import init from '../dist/opencascade_single.js';

const oc = await init({
  locateFile: (filename) => new URL(`../dist/${filename}`, import.meta.url).pathname,
});

console.log('getExceptionMessage available:', typeof oc.getExceptionMessage);

const pnts = new oc.NCollection_Array1_gp_Pnt(1, 4);
// Deliberately reproduce the earlier bug: only 2 of 4 slots set.
[[0, 0, 0], [1, 2, 0]].forEach((p, i) => pnts.SetValue(i + 1, new oc.gp_Pnt(...p)));

try {
  const splineBuilder = new oc.GeomAPI_PointsToBSpline(pnts, 3, 8, oc.GeomAbs_Shape.GeomAbs_C2, 1e-3);
  console.log('builder constructed, IsDone():', splineBuilder.IsDone());
  const bspline = splineBuilder.Curve();
  console.log('Curve() returned:', bspline);
} catch (e) {
  console.log('caught:', e, e instanceof WebAssembly.Exception);
  if (typeof oc.getExceptionMessage === 'function' && e instanceof WebAssembly.Exception) {
    console.log('decoded:', oc.getExceptionMessage(e));
  }
}

pnts.delete();
