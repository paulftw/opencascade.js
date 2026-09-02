import init from "../dist/opencascade_single.js";

const oc = await init({
  locateFile: (filename) =>
    new URL(`../dist/${filename}`, import.meta.url).pathname,
});


oc.__ocjsOnClassHandle = (record,
ptrType, 
ptr, 
        rcv,) => {
  // return
  console.log(
    // ...paylo
    ptrType.name,
    ptr,
    rcv,
    // record,
    // ptrTypeName: record.ptrType?.name,
    // smartPtr: record.smartPtr,
    // smartPtrType: record.smartPtrType?.name,
    // count: record.count?.value,
  );
};

console.log("new NCOLEECTION")
const map =
  new oc.NCollection_DataMap_TopoDS_Shape_BRepOffset_Offset_TopTools_ShapeMapHasher();

console.log("NEW BUILDER")
const builder = new oc.BRepPrimAPI_MakeCylinder(10, 20);
console.log("BILDER> SHAPE")
const shape = builder.Shape();
console.log("NEW BREP OFFSET")
const value = new oc.BRepOffset_Offset();

console.log("MAP BIND")
map.Bind(shape, value);

console.log("BOUND 1")
const a = map.Bound(shape, value);
console.log("BOUND 2")
const b = map.Bound(shape, value);

// console.log(Object.keys(a), a);
// console.log('b.$$.ptr', b.$$?.ptr);

// console.log("same C++ address:",
//   (a.mb?.tb ?? a.$$?.ptr) === (b.mb?.tb ?? b.$$?.ptr));

// console.log("same embind count:",
//   a.mb?.count === b.mb?.count);

// console.log("b before:", {
//   isDeleted: b.isDeleted(),
//   status: b.Status(),
// });

console.log("DELETE 1")

a.delete();

console.log("b AFTER a.delete():", {
  isDeleted: b.isDeleted(),
  status: b.Status(),
});
