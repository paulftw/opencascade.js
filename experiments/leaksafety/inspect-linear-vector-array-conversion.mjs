// `NCollection_LinearVector<Foo>` used to be resolved to TS `Foo[]`
// (resolver/strategies/stl.py), with no matching embind class ever compiled
// (discover.py's NCOLLECTION_CONTAINERS never included it) -- so any method
// returning it threw an embind UnboundTypeError at call time despite the
// `.d.ts` claiming a plain array. Fixed by adding it to NCOLLECTION_CONTAINERS
// and removing the stl.py short-circuit; the `.d.ts` now honestly reports the
// real bound class name (e.g. `NCollection_LinearVector_BRepGraph_ProductId`).
// This confirms: no exception, and the returned object is a genuine embind
// class_<> wrapper with the real NCollection_LinearVector API -- not an array.
import init from '../dist/opencascade_single.js';

const oc = await init({
  locateFile: (filename) => new URL(`../dist/${filename}`, import.meta.url).pathname,
});

const graph = new oc.BRepGraph();
const cylinder = new oc.BRepPrimAPI_MakeCylinder(10, 20);
const shape = cylinder.Shape();
const addResult = graph.Shapes().Add(shape);
console.log('Add().IsOk():', addResult.IsOk());

const productIds = graph.RootProductIds();

console.log('constructor.name:', productIds.constructor?.name);
console.log('Array.isArray(productIds):', Array.isArray(productIds));
console.log('productIds has .delete?', typeof productIds.delete);
console.log('productIds has [Symbol.dispose]?', typeof productIds[Symbol.dispose]);
console.log('Size():', productIds.Size());
console.log('IsEmpty():', productIds.IsEmpty());

const first = productIds.Value(0);
console.log('Value(0) constructor.name:', first?.constructor?.name);
console.log('Value(0) instanceof oc.BRepGraph_ProductId:', first instanceof oc.BRepGraph_ProductId);
console.log('Value(0).Index:', first?.Index);
console.log('Value(0).IsValid():', first?.IsValid());

// ToArray1() bridges into NCollection_Array1<BRepGraph_ProductId> -- a
// DIFFERENT instantiation than NCollection_LinearVector_BRepGraph_ProductId,
// only reachable through this container's own auto-generated method rather
// than any directly-bound OCCT class's method signature. discover.py's scan
// only walks bound classes' signatures, so this one is a separate discovery
// gap -- expected to still throw until that's fixed too.
try {
  const array1 = productIds.ToArray1();
  console.log('ToArray1() constructor.name:', array1.constructor?.name);
  console.log('ToArray1().Length():', array1.Length());
  array1.delete();
} catch (err) {
  console.log('ToArray1() threw (expected -- separate discovery gap):', err.message);
}

first?.delete();
productIds.delete();
addResult.delete();
cylinder.delete();
shape.delete();
graph.delete();
