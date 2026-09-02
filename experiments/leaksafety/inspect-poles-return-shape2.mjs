import init from '../dist/opencascade_single.js';

const oc = await init({
  locateFile: (filename) => new URL(`../dist/${filename}`, import.meta.url).pathname,
});

const pnts = new oc.NCollection_Array1_gp_Pnt(1, 4);
[[0, 0, 0], [1, 2, 0], [2, -1, 0], [3, 0, 0]].forEach((p, i) => pnts.SetValue(i + 1, new oc.gp_Pnt(...p)));
const splineBuilder = new oc.GeomAPI_PointsToBSpline(pnts, 3, 8, oc.GeomAbs_Shape.GeomAbs_C2, 1e-3);
const bspline = splineBuilder.Curve();
const poles = bspline.Poles();

console.log('Object.getOwnPropertyNames(poles):', Object.getOwnPropertyNames(poles));
console.log('Object.getOwnPropertySymbols(poles):', Object.getOwnPropertySymbols(poles).map(String));
console.log('Object.getPrototypeOf(poles) === oc.NCollection_Array1_gp_Pnt.prototype:', Object.getPrototypeOf(poles) === oc.NCollection_Array1_gp_Pnt.prototype);
console.log('Object.getOwnPropertyNames(proto):', Object.getOwnPropertyNames(Object.getPrototypeOf(poles)));
// compare against a freshly-constructed instance of the same class
console.log('--- fresh instance for comparison ---');
console.log('Object.getOwnPropertyNames(pnts):', Object.getOwnPropertyNames(pnts));
console.log('Object.getOwnPropertySymbols(pnts):', Object.getOwnPropertySymbols(pnts).map(String));

pnts.delete();
splineBuilder.delete();
bspline.delete();
poles.delete();
