import clang
from TuInfo import TuInfo
tuInfo = TuInfo("")

# def metho():
#     for child in tuInfo.allChildren:
#         if child.spelling != "V3d_SpotLight":
#             continue
#         for method in child.get_children():
#             if method.spelling != "SetPosition":
#                 continue

#             if method.kind == clang.cindex.CursorKind.USING_DECLARATION and method.access_specifier == clang.cindex.AccessSpecifier.PUBLIC:
#                 print("Found public using declaration:", method.spelling)
#                 return (child, method)
#     return None

# theClass, method = metho()
# print("Class:", theClass.spelling)
# print("Method:", method.spelling)

# for param in method.get_children():
#     if param.kind == clang.cindex.CursorKind.TYPE_REF:
#         print("Type:", param.spelling)

# ch1, ch2 = method.get_children()

# print("Child 1:", ch1.spelling)
# print("Child 2:", ch2.spelling)
# print("Child 1 kind:", ch1.kind)
# print("Child 2 kind:", ch2.kind)
# print("Child 1 type:", ch1.type.spelling)
# print("Child 2 type:", ch2.type.spelling)
# print("Child 1 type kind:", ch1.type.kind)
# print("Child 2 type kind:", ch2.type.kind)