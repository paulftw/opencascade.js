"""Unit tests for `ocjs_bindgen.codegen.result_marker`.

This module backs the MemSafety `Leaking<T>` classification (see
`MemSafety Design Doc.md`, step 1): every C++ return type that reaches
TypeScript codegen must classify as CLEANABLE, NON_CLEANABLE, or EITHER,
or the build must raise so an unhandled shape gets triaged instead of
silently mis-marked.
"""

from __future__ import annotations

import inspect

import clang.cindex
import pytest

from ocjs_bindgen.codegen.result_marker import (
  ReturnCleanability,
  assert_no_non_cleanable_fields,
  classify_return_cleanability,
)
from tests.conftest import _MockType, cursor_mock

# ---------------------------------------------------------------------------
# Primitives / void -> EITHER
# ---------------------------------------------------------------------------


def test_int_is_either():
  t = _MockType(spelling="int", kind=clang.cindex.TypeKind.INT)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


def test_void_is_either():
  t = _MockType(spelling="void", kind=clang.cindex.TypeKind.VOID)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


def test_bool_is_either():
  t = _MockType(spelling="bool", kind=clang.cindex.TypeKind.BOOL)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


# ---------------------------------------------------------------------------
# char* / const char* -> EITHER (OCJS wire-marshals these as std::string,
# i.e. a plain JS string with nothing to clean up — not a raw pointer).
# ---------------------------------------------------------------------------


def test_const_char_pointer_is_either():
  t = _MockType(spelling="const char *", kind=clang.cindex.TypeKind.POINTER)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


def test_char_pointer_is_either():
  t = _MockType(spelling="char *", kind=clang.cindex.TypeKind.POINTER)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


def test_regression_mixed_group_with_cstring_and_record_return_is_not_flagged():
  """The exact overload pair that raised a false-positive mixed-group error
  before the isCString fix: `const char* Name()` alongside
  `const TCollection_AsciiString& Name(int)`.
  """
  cstring_return = _MockType(spelling="const char *", kind=clang.cindex.TypeKind.POINTER)
  record = _MockType(spelling="TCollection_AsciiString", kind=clang.cindex.TypeKind.RECORD)
  ref_return = _MockType(
    spelling="const TCollection_AsciiString &", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=record
  )
  results = {classify_return_cleanability(t) for t in (cstring_return, ref_return)}
  assert results - {ReturnCleanability.EITHER} == {ReturnCleanability.CLEANABLE}


# ---------------------------------------------------------------------------
# Bare pointer -> NON_CLEANABLE
# ---------------------------------------------------------------------------


def test_bare_pointer_is_non_cleanable():
  t = _MockType(spelling="TopoDS_Shape *", kind=clang.cindex.TypeKind.POINTER)
  assert classify_return_cleanability(t) == ReturnCleanability.NON_CLEANABLE


# ---------------------------------------------------------------------------
# Record (class/struct by value) -> CLEANABLE
# ---------------------------------------------------------------------------


def test_record_by_value_is_cleanable():
  t = _MockType(spelling="TopoDS_Shape", kind=clang.cindex.TypeKind.RECORD)
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


# ---------------------------------------------------------------------------
# References -> CLEANABLE, except reference-to-array (raises) and
# reference-to-pointer (NON_CLEANABLE, same as bare pointer)
# ---------------------------------------------------------------------------


def test_reference_to_record_is_cleanable():
  pointee = _MockType(spelling="TopoDS_Shape", kind=clang.cindex.TypeKind.RECORD)
  t = _MockType(spelling="TopoDS_Shape &", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=pointee)
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


def test_reference_to_primitive_is_cleanable_tolerated_false_positive():
  """A `const int&` return is wire-marshaled by embind as a bare JS number
  with nothing to delete() — this classification is a known, accepted
  over-approximation (see result_marker.py's `classify_return_cleanability`
  docstring / the MemSafety design discussion), not a bug to "fix" here.
  """
  pointee = _MockType(spelling="int", kind=clang.cindex.TypeKind.INT)
  t = _MockType(spelling="const int &", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=pointee)
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


def test_reference_to_pointer_is_non_cleanable():
  """`const Xxx_PYyy&` (OCCT's `Foo*&` internal-pointer-member accessor
  idiom, e.g. `BRepAlgoAPI_BuilderAlgo::Builder()` returning
  `const BOPAlgo_PBuilder&`) is wire-marshaled the same way a bare `Foo*`
  return already is: `needsCStringOrLvalueWrapper` (bindings.py) routes it
  through `emscripten::val(ret, allow_raw_pointers())`, and `auto ret = ...`
  copies the pointer *value* — same non-owning aliased handle either way.
  """
  pointee = _MockType(spelling="TopoDS_Shape *", kind=clang.cindex.TypeKind.POINTER)
  t = _MockType(spelling="TopoDS_Shape *&", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=pointee)
  assert classify_return_cleanability(t) == ReturnCleanability.NON_CLEANABLE


def test_reference_to_array_raises():
  pointee = _MockType(spelling="int [3]", kind=clang.cindex.TypeKind.CONSTANTARRAY)
  t = _MockType(spelling="int (&)[3]", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=pointee)
  with pytest.raises(Exception, match="Unsupported reference return"):
    classify_return_cleanability(t)


# ---------------------------------------------------------------------------
# Dependent member type resolution via templateArgs — the
# BRepGraph_ReverseIterator::EdgeParentsOf<TraitsT> case: a method returns
# `ParentId`, where `using ParentId = typename TraitsT::ParentId;` inside a
# class template and `TraitsT` is substituted at this call site.
# ---------------------------------------------------------------------------


def _dependent_member_type_fixture(member_underlying):
  """Build the (t, templateArgs) pair for `ParentId` resolving, via
  `TraitsT`, to a typedef whose underlying type is `member_underlying`.
  """
  member_typedef_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPEDEF_DECL,
    spelling="ParentId",
    underlying_typedef_type=member_underlying,
  )
  traits_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CLASS_DECL,
    spelling="WireFromEdgeCoEdgeTraits",
    children=[member_typedef_cursor],
  )
  traits_type = _MockType(
    spelling="WireFromEdgeCoEdgeTraits", kind=clang.cindex.TypeKind.RECORD, declaration=traits_class_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ParentId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPEDEF_DECL,
    spelling="ParentId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ParentId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)
  return t, {"TraitsT": traits_type}


def test_dependent_member_type_resolves_to_concrete_cleanable():
  record = _MockType(spelling="BRepGraph_WireId", kind=clang.cindex.TypeKind.RECORD)
  t, templateArgs = _dependent_member_type_fixture(record)
  assert classify_return_cleanability(t, templateArgs) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_resolves_to_concrete_either():
  primitive = _MockType(spelling="int", kind=clang.cindex.TypeKind.INT)
  t, templateArgs = _dependent_member_type_fixture(primitive)
  assert classify_return_cleanability(t, templateArgs) == ReturnCleanability.EITHER


def test_dependent_member_type_without_template_args_still_raises():
  """No templateArgs at all (the default) -- falls through to the same
  raise as before this resolution path existed. No silent regression.
  """
  record = _MockType(spelling="BRepGraph_WireId", kind=clang.cindex.TypeKind.RECORD)
  t, _templateArgs = _dependent_member_type_fixture(record)
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t)


def test_dependent_member_type_with_unrelated_template_args_still_raises():
  """templateArgs is present but doesn't contain the dependent type's own
  parent name -- falls through to the same raise, not a false resolve.
  """
  record = _MockType(spelling="BRepGraph_WireId", kind=clang.cindex.TypeKind.RECORD)
  t, _templateArgs = _dependent_member_type_fixture(record)
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t, {"SomeOtherParam": _MockType(spelling="Whatever")})


def test_dependent_member_type_resolves_through_base_class():
  """The BRepGraph_DefsIterator::ShellOfSolidTraits case: the member typedef
  (`ChildId`) isn't a direct child of the substituted traits class -- it's
  inherited from a shared `BaseTraits<...>` the traits class derives from.
  """
  member_typedef_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPEDEF_DECL,
    spelling="ChildId",
    underlying_typedef_type=_MockType(spelling="BRepGraph_SolidId", kind=clang.cindex.TypeKind.RECORD),
  )
  base_traits_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="BaseTraits",
    children=[member_typedef_cursor],
  )
  base_specifier_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CXX_BASE_SPECIFIER,
    spelling="BaseTraits",
    get_definition=lambda: base_traits_cursor,
  )
  traits_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="ShellOfSolidTraits",
    children=[base_specifier_cursor],  # no direct ChildId typedef of its own
  )
  traits_type = _MockType(
    spelling="ShellOfSolidTraits", kind=clang.cindex.TypeKind.RECORD, declaration=traits_class_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ChildId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ChildId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ChildId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)

  assert classify_return_cleanability(t, {"TraitsT": traits_type}) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_resolves_through_base_class_via_type_fallback():
  """Same ShellOfSolidTraits/BaseTraits shape, but `get_definition()` on the
  CXX_BASE_SPECIFIER returns nothing usable (as it does for a base that's
  itself a template instantiation, e.g. `: public BaseTraits<Args...>`) --
  must fall back to `child.type.get_declaration()` and still resolve.
  """
  member_typedef_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPEDEF_DECL,
    spelling="ChildId",
    underlying_typedef_type=_MockType(spelling="BRepGraph_SolidId", kind=clang.cindex.TypeKind.RECORD),
  )
  base_traits_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="BaseTraits",
    children=[member_typedef_cursor],
  )
  base_specifier_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CXX_BASE_SPECIFIER,
    spelling="BaseTraits",
    get_definition=lambda: None,  # simulates get_definition() failing for a templated base
    type=_MockType(declaration=base_traits_cursor),
  )
  traits_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="ShellOfSolidTraits",
    children=[base_specifier_cursor],
  )
  traits_type = _MockType(
    spelling="ShellOfSolidTraits", kind=clang.cindex.TypeKind.RECORD, declaration=traits_class_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ChildId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ChildId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ChildId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)

  assert classify_return_cleanability(t, {"TraitsT": traits_type}) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_resolves_through_non_defining_reference_cursor():
  """`substituted_type.get_declaration()` (or a queued base's own decl) can
  hand back a non-defining reference cursor with no visible children, even
  though a real definition with the base specifier/members exists elsewhere
  -- must normalize via `get_definition()` before walking children.
  """
  member_typedef_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPEDEF_DECL,
    spelling="ChildId",
    underlying_typedef_type=_MockType(spelling="BRepGraph_SolidId", kind=clang.cindex.TypeKind.RECORD),
  )
  # The REAL definition, with children -- reachable only via get_definition().
  defining_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="ShellOfSolidTraits",
    children=[member_typedef_cursor],
  )
  # A non-defining reference cursor: same spelling, but empty children, and
  # its own get_definition() is the only way to reach `defining_cursor`.
  reference_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="ShellOfSolidTraits",
    children=[],
    get_definition=lambda: defining_cursor,
  )
  traits_type = _MockType(
    spelling="ShellOfSolidTraits", kind=clang.cindex.TypeKind.RECORD, declaration=reference_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ChildId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ChildId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ChildId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)

  assert classify_return_cleanability(t, {"TraitsT": traits_type}) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_falls_back_to_cleanable_for_known_safe_unreadable_base():
  """The real, unresolved BRepGraph_DefsShellOfSolid case: `ShellOfSolidTraits`
  inherits `ChildId` from `BaseTraits<Args...>`, but neither `get_definition()`
  nor `type.get_declaration()` (nor classDict) can materialize `BaseTraits`'s
  body -- confirmed even the reference TS resolver silently emits `unknown`
  here. Falls back to the known-safe-base allowlist instead of raising.
  """
  # `BaseTraits` resolves, but its own children are empty either way --
  # exactly the "lazy instantiation" symptom hit on the real AST.
  unreadable_base_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="BaseTraits",
    children=[],
  )
  base_specifier_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CXX_BASE_SPECIFIER,
    spelling="BaseTraits<Args...>",
    get_definition=lambda: unreadable_base_cursor,
  )
  traits_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="ShellOfSolidTraits",
    children=[base_specifier_cursor],
  )
  traits_type = _MockType(
    spelling="ShellOfSolidTraits", kind=clang.cindex.TypeKind.RECORD, declaration=traits_class_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ChildId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ChildId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ChildId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)

  # No classDict entry for "BaseTraits" either -- matches the real crash.
  assert classify_return_cleanability(t, {"TraitsT": traits_type}, classDict={}) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_unreadable_base_not_in_allowlist_still_raises():
  """An unreadable base with a DIFFERENT name (not `BaseTraits`) must not be
  silently assumed safe -- only the specific, inspected allowlist entries
  get the fallback.
  """
  unreadable_base_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="SomeOtherUnrelatedBase",
    children=[],
  )
  base_specifier_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CXX_BASE_SPECIFIER,
    spelling="SomeOtherUnrelatedBase<Args...>",
    get_definition=lambda: unreadable_base_cursor,
  )
  traits_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.STRUCT_DECL,
    spelling="SomeDerivedTraits",
    children=[base_specifier_cursor],
  )
  traits_type = _MockType(
    spelling="SomeDerivedTraits", kind=clang.cindex.TypeKind.RECORD, declaration=traits_class_cursor
  )
  alias_underlying = _MockType(spelling="typename TraitsT::ChildId")
  alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ChildId",
    underlying_typedef_type=alias_underlying,
  )
  t = _MockType(spelling="ChildId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=alias_decl_cursor)

  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t, {"TraitsT": traits_type}, classDict={})


def test_dependent_member_type_resolves_via_sibling_member_alias_not_template_arg():
  """The real BRepGraph_RefsIterator::RefIterator case: `parent_name` isn't
  a template parameter at all. `RefIterator<RefType, TheFullTraverse>`
  declares `using Traits = Detail::RefTraits<RefType>;` then
  `using RefId = typename Traits::RefId;` -- `Traits` is a sibling member
  alias of the *enclosing* class. `RefTraits<T>` itself is forward-declared
  only (`template <typename T> struct RefTraits;`, no generic body, only
  explicit per-RefType specializations), so libclang can't materialize any
  members on it either -- falls back to the known-safe-type allowlist, same
  as the `BaseTraits` case but reached via the sibling-alias path instead of
  inheritance.
  """
  ref_traits_template_param = cursor_mock(
    kind=clang.cindex.CursorKind.TEMPLATE_TYPE_PARAMETER,
    spelling="T",
  )
  ref_traits_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CLASS_TEMPLATE,
    spelling="RefTraits",
    # A bare forward declaration (`template <typename T> struct RefTraits;`)
    # still carries its template-parameter cursor as a child even with no
    # body -- confirmed against the real crash's diagnostics, which showed
    # `_find_member_typedef(parent_decl, 'RefId') -> not found` rather than
    # falling back, because an earlier version of this fixture (and of
    # `_find_member_typedef`) wrongly assumed `children=[]`.
    children=[ref_traits_template_param],
  )
  ref_traits_type = _MockType(
    spelling="Detail::RefTraits<RefType>", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=ref_traits_cursor
  )
  traits_alias_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="Traits",
    underlying_typedef_type=ref_traits_type,
  )
  ref_iterator_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CLASS_DECL,
    spelling="RefIterator",
    children=[traits_alias_cursor],
  )
  refid_underlying = _MockType(spelling="typename Traits::RefId")
  refid_alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="RefId",
    underlying_typedef_type=refid_underlying,
    parent=ref_iterator_class_cursor,
  )
  t = _MockType(spelling="RefId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=refid_alias_decl_cursor)

  templateArgs = {
    "RefType": _MockType(spelling="BRepGraphInc::ShellRef"),
    "TheFullTraverse": _MockType(spelling="false"),
  }
  assert classify_return_cleanability(t, templateArgs, classDict={}) == ReturnCleanability.CLEANABLE


def test_dependent_member_type_sibling_alias_to_non_allowlisted_unreadable_type_still_raises():
  """Same sibling-alias shape as above, but the aliased type's name isn't in
  the allowlist -- must still raise rather than silently assuming safety for
  an arbitrary unreadable local alias.
  """
  other_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CLASS_TEMPLATE,
    spelling="SomeOtherUnrelatedTraits",
    children=[],
  )
  other_type = _MockType(
    spelling="Detail::SomeOtherUnrelatedTraits<RefType>",
    kind=clang.cindex.TypeKind.UNEXPOSED,
    declaration=other_cursor,
  )
  alias_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="Traits",
    underlying_typedef_type=other_type,
  )
  enclosing_class_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.CLASS_DECL,
    spelling="SomeIterator",
    children=[alias_cursor],
  )
  refid_underlying = _MockType(spelling="typename Traits::RefId")
  refid_alias_decl_cursor = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="RefId",
    underlying_typedef_type=refid_underlying,
    parent=enclosing_class_cursor,
  )
  t = _MockType(spelling="RefId", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=refid_alias_decl_cursor)

  templateArgs = {"RefType": _MockType(spelling="BRepGraphInc::ShellRef")}
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t, templateArgs, classDict={})


def test_bare_template_parameter_reference_resolves_via_template_args():
  """`t` may be a bare, un-aliased reference to the enclosing template's own
  parameter -- e.g. `template <typename ContainerT, typename TypedIdT>
  class Foo { TypedIdT Method(); };` returning `TypedIdT` directly, no
  `typename X::Y` indirection at all. `t.get_declaration()` is
  NO_DECL_FOUND here (it's not a typedef), so neither
  `_is_class_template_instantiation` nor `_resolve_dependent_member_type`
  apply -- resolved instead by matching `t.spelling` directly against a
  `templateArgs` key and substituting.
  """
  no_decl_cursor = cursor_mock(kind=clang.cindex.CursorKind.NO_DECL_FOUND, spelling="")
  t = _MockType(spelling="TypedIdT", kind=clang.cindex.TypeKind.UNEXPOSED, declaration=no_decl_cursor)
  substituted = _MockType(spelling="BRepGraph_ShellId", kind=clang.cindex.TypeKind.RECORD)
  templateArgs = {"ContainerT": _MockType(spelling="SomeContainer"), "TypedIdT": substituted}

  assert classify_return_cleanability(t, templateArgs) == ReturnCleanability.CLEANABLE


# ---------------------------------------------------------------------------
# Class template instantiation (`TemplateName<Args>`, still dependent /
# UNEXPOSED) -- always a class if TemplateName's own declaration is a real
# class template, regardless of whose template it is. Covers both the
# self-reference shape (`NCollection_UtfString::SubString()` returning bare
# `NCollection_UtfString` inside its own body) and a method returning a
# *different*, sibling class template instantiated with the enclosing
# template's own parameter (`NCollection_LinearVector<TheItemType>::ToArray1()`
# returning `NCollection_Array1<TheItemType>`) -- both are ordinary class
# template instantiations, not textual aliasing.
# ---------------------------------------------------------------------------


def _class_template_type(spelling, decl_kind=clang.cindex.CursorKind.CLASS_TEMPLATE):
  decl_cursor = cursor_mock(kind=decl_kind, spelling=spelling.split("<", 1)[0])
  return _MockType(spelling=spelling, kind=clang.cindex.TypeKind.UNEXPOSED, declaration=decl_cursor)


def test_template_self_reference_is_cleanable():
  t = _class_template_type("NCollection_UtfString<Type>")
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


def test_sibling_class_template_instantiation_is_cleanable():
  """The NCollection_LinearVector::ToArray1() case: a *different* class
  template (NCollection_Array1) instantiated with the enclosing template's
  own parameter -- not self-reference, still a genuine class.
  """
  t = _class_template_type("NCollection_Array1<TheItemType>")
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


def test_class_template_instantiation_via_class_decl_is_cleanable():
  """get_declaration() may resolve to CLASS_DECL/STRUCT_DECL rather than
  CLASS_TEMPLATE depending on how fully clang resolved this specialization
  -- both count as a real class."""
  t = _class_template_type("SomeTemplate<Args>", decl_kind=clang.cindex.CursorKind.STRUCT_DECL)
  assert classify_return_cleanability(t) == ReturnCleanability.CLEANABLE


def test_class_template_instantiation_reached_via_canonical_alias_peel_is_cleanable():
  """The real BRepGraph_ReverseIterator::LookupParentRefsOf<TraitsT> case:
  `using ResultType = ParentRef<ParentIdType, RefIdType>;`. `t` itself is
  the alias (`t.spelling == "ResultType"`, no `<`), so
  `_is_class_template_instantiation(t)` alone doesn't see the
  template-instantiation shape at all -- only `t.get_canonical()` (which
  fully expands through the alias) shows `ParentRef<...>`. `ParentRef` is an
  ordinary, readable class template (`struct ParentRef { ParentIdT
  ParentId; RefIdT Ref; };`) -- no allowlist needed, just checking the right
  spelling.
  """
  parent_ref_decl = cursor_mock(kind=clang.cindex.CursorKind.CLASS_TEMPLATE, spelling="ParentRef")
  canonical = _MockType(
    spelling="ParentRef<typename TraitsT::ParentId, typename TraitsT::RefId>",
    kind=clang.cindex.TypeKind.UNEXPOSED,
    declaration=parent_ref_decl,
  )
  alias_decl = cursor_mock(
    kind=clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    spelling="ResultType",
    underlying_typedef_type=_MockType(spelling="ParentRef<ParentIdType, RefIdType>"),
  )
  t = _MockType(
    spelling="ResultType",
    kind=clang.cindex.TypeKind.UNEXPOSED,
    declaration=alias_decl,
    canonical=canonical,
  )

  templateArgs = {"TraitsT": _MockType(spelling="SomeTraits")}
  assert classify_return_cleanability(t, templateArgs) == ReturnCleanability.CLEANABLE


def test_alias_template_instantiation_not_assumed_cleanable():
  """The safety case this whole check exists to exclude: an alias template
  (`template<typename T> using Foo = T*;`) is pure textual substitution --
  TemplateName<Args> is NOT unconditionally a class just because it has the
  `<...>` shape. Must still raise, not silently default to CLEANABLE.
  """
  t = _class_template_type("SomePointerAlias<Type>", decl_kind=clang.cindex.CursorKind.TYPE_ALIAS_TEMPLATE_DECL)
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t)


def test_unexposed_non_template_type_still_raises():
  """No `<` in the spelling at all -- not a template instantiation shape,
  falls through to the same raise as before this resolution path existed.
  """
  t = _MockType(spelling="SomeDependentThing", kind=clang.cindex.TypeKind.UNEXPOSED)
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t)


# ---------------------------------------------------------------------------
# Unknown kind -> raise (step 1's "not in OCJS yet" contract)
# ---------------------------------------------------------------------------


def test_unrecognized_kind_raises():
  t = _MockType(spelling="some_weird_type", kind=clang.cindex.TypeKind.MEMBERPOINTER)
  with pytest.raises(Exception, match="Unknown return ownership"):
    classify_return_cleanability(t)


# ---------------------------------------------------------------------------
# assert_no_non_cleanable_fields -> RBV envelope field validation (step 3)
# ---------------------------------------------------------------------------


def test_assert_passes_with_only_cleanable_and_either_fields():
  record = _MockType(spelling="TopoDS_Shape", kind=clang.cindex.TypeKind.RECORD)
  primitive = _MockType(spelling="bool", kind=clang.cindex.TypeKind.BOOL)
  assert_no_non_cleanable_fields([record, primitive])


def test_assert_passes_with_empty_list():
  assert_no_non_cleanable_fields([])


def test_assert_raises_on_non_cleanable_field():
  record = _MockType(spelling="TopoDS_Shape", kind=clang.cindex.TypeKind.RECORD)
  pointer = _MockType(spelling="TopoDS_Shape *", kind=clang.cindex.TypeKind.POINTER)
  with pytest.raises(Exception, match="not cleanable"):
    assert_no_non_cleanable_fields([record, pointer])


def test_assert_propagates_raise_for_unclassifiable_field():
  """A field type result_marker.py itself can't classify (e.g. `Foo[]&`)
  must still surface as a build-breaking raise, not get silently skipped.
  """
  array_pointee = _MockType(spelling="int [3]", kind=clang.cindex.TypeKind.CONSTANTARRAY)
  ref_to_array = _MockType(spelling="int (&)[3]", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=array_pointee)
  with pytest.raises(Exception, match="Unsupported reference return"):
    assert_no_non_cleanable_fields([ref_to_array])


def test_assert_raises_on_reference_to_pointer_field():
  """A `Foo*&` field (now NON_CLEANABLE, not a raise from classification
  itself) must still trip the "no non-cleanable fields in an RBV envelope"
  rule — it's exactly the kind of field this assert exists to catch.
  """
  ptr_pointee = _MockType(spelling="TopoDS_Shape *", kind=clang.cindex.TypeKind.POINTER)
  ref_to_pointer = _MockType(
    spelling="TopoDS_Shape *&", kind=clang.cindex.TypeKind.LVALUEREFERENCE, pointee=ptr_pointee
  )
  with pytest.raises(Exception, match="not cleanable"):
    assert_no_non_cleanable_fields([ref_to_pointer])


# ---------------------------------------------------------------------------
# Enum -> EITHER, *only* because OCJS hardcodes the string enum variant.
# ---------------------------------------------------------------------------


def test_enum_is_either():
  t = _MockType(spelling="GeomAbs_SurfaceType", kind=clang.cindex.TypeKind.ENUM)
  assert classify_return_cleanability(t) == ReturnCleanability.EITHER


def test_ocjs_still_registers_enums_as_the_string_variant():
  """Tripwire, not a style check.

  `classify_return_cleanability` classifies every C++ enum return as
  EITHER (= never wrapped in `Leaking<T>`, never routed through `fr()`).
  That is only correct because `embind/enum.py` hardcodes every OCJS enum
  registration to `emscripten::enum_value_type::string`, and libembind's
  `string`-variant enums are plain, non-owning JS string primitives with
  `destructorFunction: null` — there is nothing to leak.

  libembind also supports an `object` variant where each enum member is a
  singleton JS object cached on the enum's constructor (see
  `pristine-libembind.js`, `_embind_register_enum_value`, the `'object'`
  case). If OCJS ever binds an enum that way, the EITHER classification
  above becomes wrong.

  If this test fails because someone changed `embind/enum.py`'s hardcoded
  `enum_value_type::string`: do NOT just update the literal this test
  checks for. Go re-derive whether `ReturnCleanability.EITHER` for ENUM in
  `result_marker.py` is still correct for the new variant, and update
  `classify_return_cleanability` (and this test) together.
  """
  import ocjs_bindgen.codegen.embind.enum as embind_enum_mod

  source = inspect.getsource(embind_enum_mod)
  assert "emscripten::enum_value_type::string" in source, (
    "embind/enum.py no longer hardcodes the string enum variant — "
    "result_marker.py's `ENUM -> ReturnCleanability.EITHER` classification "
    "was only correct for the string variant (see this test's docstring). "
    "Re-derive the correct classification before touching this assertion."
  )
