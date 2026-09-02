import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collections import Counter

import clang.cindex

from ocjs_bindgen.ast.parse import parse
from ocjs_bindgen.predicates import shouldProcessClass
from ocjs_bindgen.filters.method_or_properties import filterMethodOrProperty


OCCT_BASE_PATH = "/occt/src/"

SAFE = "safe"
UNSAFE = "unsafe"
LOW = "low-confidence"
UNKNOWN = "unknown"
NONOBJECT = "non-object"


def canonical(t):
    return t.get_canonical()
def spelling(t):
    return canonical(t).spelling


def is_handle(t):
    return (
        spelling(t).startswith("opencascade::handle<") 
        or spelling(t).startswith("const opencascade::handle<")
    )


def is_std_optional(t):
    return spelling(t).startswith("std::optional<")


def is_std_pair(t):
    return spelling(t).startswith("std::pair<")


def is_std_reference_wrapper(t):
    return spelling(t).startswith("std::reference_wrapper<")


def template_arg(t, index):
    return canonical(t).get_template_argument_type(index)


def combine_pair(a, b):
    if a == UNSAFE and b == UNSAFE:
        return SAFE

    if a == SAFE and b == SAFE:
        return SAFE

    if UNKNOWN in (a, b) or a != b:
        return UNKNOWN

    return LOW


def classify_type(t):
    t = canonical(t)

    # No cleanup question.
    if t.kind in (
        clang.cindex.TypeKind.VOID,
        clang.cindex.TypeKind.BOOL,
        clang.cindex.TypeKind.INT,
        clang.cindex.TypeKind.INT128,
        clang.cindex.TypeKind.UINT,
        clang.cindex.TypeKind.UINT128,
        clang.cindex.TypeKind.UCHAR,
        clang.cindex.TypeKind.CHAR_U,
        clang.cindex.TypeKind.CHAR_S,
        clang.cindex.TypeKind.USHORT,
        clang.cindex.TypeKind.LONG,
        clang.cindex.TypeKind.ULONG,
        clang.cindex.TypeKind.LONGLONG,
        clang.cindex.TypeKind.ULONGLONG,
        clang.cindex.TypeKind.DOUBLE,
        clang.cindex.TypeKind.FLOAT,
        clang.cindex.TypeKind.LONGDOUBLE,
        clang.cindex.TypeKind.CHAR16,
        clang.cindex.TypeKind.CHAR32,
        clang.cindex.TypeKind.ENUM,
    ):
        return NONOBJECT

    # Borrowed storage.
    if t.kind in (
        clang.cindex.TypeKind.POINTER,
        clang.cindex.TypeKind.LVALUEREFERENCE,
        clang.cindex.TypeKind.RVALUEREFERENCE,
    ):
        return UNSAFE

    # OCCT intrusive smart handle.
    if is_handle(t):
        return SAFE

    # Explicit borrowed wrapper.
    if is_std_reference_wrapper(t):
        return UNSAFE

    # Value wrapper: ownership follows the contained value.
    if is_std_optional(t):
        inner = classify_type(template_arg(t, 0))

        if inner == UNSAFE:
            return UNSAFE

        if inner == SAFE:
            return SAFE

        return LOW

    if is_std_pair(t):
        return combine_pair(
            classify_type(template_arg(t, 0)),
            classify_type(template_arg(t, 1)),
        )

    # Ordinary by-value OCCT class/struct that OCJS knows how to bind.
    decl = t.get_declaration()

    if decl is not None and shouldProcessClass(decl, OCCT_BASE_PATH):
        return SAFE

    # We have a concrete record type, but cannot prove the cleanup semantics
    # from the rules above.
    if t.kind == clang.cindex.TypeKind.RECORD:
        return UNKNOWN

    return UNKNOWN


tu = parse()

counts = Counter()

low_types = Counter()
unknown_types = Counter()

low_methods = []
unknown_methods = []

for child in tu.cursor.get_children():
    if child.kind != clang.cindex.CursorKind.CLASS_DECL:
        continue

    if not shouldProcessClass(child, OCCT_BASE_PATH):
        continue

    for method in child.get_children():
        if (
            method.kind != clang.cindex.CursorKind.CXX_METHOD
            or method.access_specifier != clang.cindex.AccessSpecifier.PUBLIC
        ):
            continue

        # Only methods OCJS would actually expose.
        if not filterMethodOrProperty(child, method):
            continue

        classification = classify_type(method.result_type)
        counts[classification] += 1

        signature = (
            f"{child.spelling}::"
            f"{method.result_type.spelling} "
            f"{method.spelling}("
            f"{', '.join(arg.type.spelling for arg in method.get_arguments())}"
            f")"
        )

        result_type = canonical(method.result_type).spelling

        if classification == LOW:
            low_types[result_type] += 1
            low_methods.append(signature)

        elif classification == UNKNOWN:
            unknown_types[result_type] += 1
            unknown_methods.append(signature)


print("RETURN CLEANUP CONFIDENCE")
print("=========================")

for category in (
    SAFE,
    UNSAFE,
    LOW,
    UNKNOWN,
    NONOBJECT,
):
    print(f"{category:15} {counts[category]}")

print()
print("TOTAL")
print("=====")
print(sum(counts.values()))

print()
print("LOW-CONFIDENCE TYPES")
print("====================")

for spelling, count in low_types.most_common():
    print(f"{count:5}  {spelling}")

print()
print("UNKNOWN TYPES")
print("=============")

for spelling, count in unknown_types.most_common():
    print(f"{count:5}  {spelling}")

print()
print("LOW-CONFIDENCE METHOD SIGNATURES")
print("================================")

for signature in sorted(low_methods):
    print(signature)

print()
print("UNKNOWN METHOD SIGNATURES")
print("==========================")

for signature in sorted(unknown_methods):
    print(signature)