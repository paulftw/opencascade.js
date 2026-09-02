import clang.cindex
from collections import defaultdict

tu = clang.cindex.TranslationUnit.from_ast_file("/tmp/ocjs-main-tu.ast")


from collections import Counter

all_unsafe = Counter()


primitive_types = {
    "void", "bool",
    "char", "signed char", "unsigned char",
    "wchar_t", "char8_t", "char16_t", "char32_t",
    "short", "unsigned short",
    "int", "unsigned int",
    "long", "unsigned long",
    "long long", "unsigned long long",
    "float", "double", "long double",
}


def return_info(t):
    ct = t.get_canonical()

    if ct.kind == clang.cindex.TypeKind.POINTER:
        return ("unsafe", "pointer", t.spelling)

    if t.kind in (
        clang.cindex.TypeKind.LVALUEREFERENCE,
        clang.cindex.TypeKind.RVALUEREFERENCE,
    ):
        return ("safe", "reference", t.spelling)

    if t.get_canonical().spelling in primitive_types:
        return ("safe", "value", t.spelling)

    if (
        ct.kind == clang.cindex.TypeKind.RECORD
        and ct.get_declaration()
        and ct.get_declaration().spelling == "handle"
        and ct.get_num_template_arguments() == 1
    ):
        return ("safe", "handle", ct.get_template_argument_type(0).spelling)

    if t.get_canonical().kind in (
        clang.cindex.TypeKind.RECORD,
        clang.cindex.TypeKind.ENUM,
    ):
        return ("safe", "value", t.get_canonical().spelling)

    # all_unknowns[("value", ct.spelling)] += 1
    # all_unknowns[(
    #     t.spelling,
    #     t.kind,
    #     t.get_canonical().spelling,
    #     t.get_canonical().kind,
    #     t.get_declaration().spelling if t.get_declaration() else None,
    #     t.get_num_template_arguments(),
    # )] += 1
    raise Exception("unknown type: " + ct.spelling)


for cls in tu.cursor.get_children():
    if cls.kind not in (
        clang.cindex.CursorKind.CLASS_DECL,
        clang.cindex.CursorKind.STRUCT_DECL,
    ):
        continue

    methods = defaultdict(list)

    # First collect all public methods, grouped by name.
    for m in cls.get_children():
        if (
            m.kind == clang.cindex.CursorKind.CXX_METHOD
            and m.access_specifier == clang.cindex.AccessSpecifier.PUBLIC
            and not m.spelling.startswith("operator")
        ):
            methods[m.spelling].append(m)

    # Now analyze the overload groups.
    for name, overloads in methods.items():
        infos = [return_info(m.result_type) for m in overloads]

        unsafe = [
            (m, info)
            for m, info in zip(overloads, infos)
            if info[0] == "unsafe"
        ]

        if not unsafe:
            continue

        for m, info in unsafe:
            all_unsafe[m.result_type.spelling] += 1

        print(
            f"\n{cls.spelling}::{name}"
            f"  [{len(overloads)} overload{'s' if len(overloads) != 1 else ''}]"
        )

        for m, info in unsafe:
            args = ", ".join(
                a.type.spelling
                for a in m.get_arguments()
            )

            # signature = (
            #     f"{m.result_type.spelling} "
            #     f"{cls.spelling}::{name}({args})"
            # )

            # print(f"  UNSAFE: {signature}")

for t, n in all_unsafe.most_common():
    print(n, t)

xxx()



for cls in tu.cursor.get_children():
    if cls.kind not in (
        clang.cindex.CursorKind.CLASS_DECL,
        clang.cindex.CursorKind.STRUCT_DECL,
    ):
        continue

    methods = defaultdict(list)

    for name, overloads in methods.items():
        has_output_params = any(
            isOutputParam(a.type)
            for m in overloads
            for a in m.get_arguments()
        )

        if has_output_params and len(overloads) != 1:
            print(f"\n{cls.spelling}::{name} has {len(overloads)} overloads")
            for m in overloads:
                print(
                    " ",
                    m.result_type.spelling,
                    "(",
                    ", ".join(a.type.spelling for a in m.get_arguments()),
                    ")"
                )
            raise Exception("unexpected output-param overload group")


    for m in cls.get_children():
        if (
            m.kind == clang.cindex.CursorKind.CXX_METHOD
            and m.access_specifier == clang.cindex.AccessSpecifier.PUBLIC
            and not m.spelling.startswith("operator")
        ):
            methods[m.spelling].append(m)

    for name, overloads in methods.items():
        # if len(overloads) < 2:
        #     continue

        infos = [return_info(m.result_type) for m in overloads]
        statuses = {x[0] for x in infos}
        

        if len(statuses) > 1:
            print(f"\n{cls.spelling}::{name}")
            for m, info in zip(overloads, infos):
                args = ", ".join(a.type.spelling for a in m.get_arguments())
                print(f"  {info}  {name}({args})")




print("##################################################################\n")
print("##################################################################\n")
print("##################################################################\n")
print("##################################################################\n")


from collections import Counter

import sys
sys.path.insert(0, 'src')

output_original_returns = Counter()
output_arg_types = Counter()
from ocjs_bindgen.predicates.args import isOutputParam

for cls in tu.cursor.get_children():
    if cls.kind not in (
        clang.cindex.CursorKind.CLASS_DECL,
        clang.cindex.CursorKind.STRUCT_DECL,
    ):
        continue

    for m in cls.get_children():
        if not (
            m.kind == clang.cindex.CursorKind.CXX_METHOD
            and m.access_specifier == clang.cindex.AccessSpecifier.PUBLIC
            and not m.spelling.startswith("operator")
        ):
            continue

        args = list(m.get_arguments())

        output_args = [
            a for a in args
            if isOutputParam(a.type)
        ]

        if output_args:
            output_original_returns[m.result_type.spelling] += 1

            for a in output_args:
                output_arg_types[a.type.spelling] += 1



print("\n=== ORIGINAL RETURNS OF OUTPUT-PARAM METHODS ===")
for t, n in output_original_returns.most_common():
    print(n, t)

print("\n=== OUTPUT PARAM TYPES ===")
for t, n in output_arg_types.most_common():
    print(n, t)