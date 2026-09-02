import enum
import sys

import clang.cindex

from ocjs_bindgen.predicates.types import isCString


def _raise(msg):
  """Print to stderr before raising — several callers wrap this module's
  functions in a broad `except Exception` (e.g. `_render_synthesized_base_signature`
  swallowing exceptions into a dropped signature), which would otherwise
  hide these raises entirely. The print guarantees visibility even then.
  """
  print(f"MemSafety: {msg}", file=sys.stderr)
  raise Exception(msg)


primitive_types = {
  "void",
  "bool",
  "char",
  "signed char",
  "unsigned char",
  "wchar_t",
  "char8_t",
  "char16_t",
  "char32_t",
  "short",
  "unsigned short",
  "int",
  "unsigned int",
  "long",
  "unsigned long",
  "long long",
  "unsigned long long",
  "float",
  "double",
  "long double",
}


class ReturnCleanability(enum.Enum):
  CLEANABLE = "cleanable"  # Foo, Foo& (incl. RBV envelope fields) -> Leaking<T>
  NON_CLEANABLE = "non_cleanable"  # Foo* (bare pointer) -> plain T, never wrapped
  EITHER = "either"  # void, undefined, primitive -> ignored in consistency checks


# Class names whose body libclang can't materialize here (confirmed: even
# the reference TS resolver — resolve_qualified_member_type — silently
# fails on these too, emitting `unknown` rather than crashing; see
# BRepGraph_DefsShellOfSolid.d.ts.json's `CurrentId(): unknown;`), but whose
# *every* member across *every* real occurrence in deps/OCCT is verified by
# inspection to be a plain OCCT value type — never a pointer. Checked in
# `_find_member_typedef` at the moment a cursor by this name is popped off
# the walk queue, regardless of how it was reached: as a base specifier
# (BaseTraits — inherited from) or as the walk's own root (RefTraits —
# reached via a sibling member alias, see `_resolve_dependent_member_type`'s
# enclosing-class fallback).
#
# - BaseTraits (BRepGraph_DefsIterator.hxx: 5-param, ParentId/RefId/RefEntry/
#   ChildId/ChildDef; BRepGraph_RefsIterator.hxx: 3-param, ParentId/RefId/
#   RefEntry only) — two distinct, unrelated structs, both checked, both safe.
# - RefTraits (BRepGraph_RefsIterator.hxx: forward-declared only —
#   `template <typename T> struct RefTraits;`, no generic body — with 7
#   explicit specializations, one per RefType, each declaring only
#   `using RefId = BRepGraph_XxxRefId;`). All 7 `BRepGraph_XxxRefId` aliases
#   checked (BRepGraph_RefId.hxx) — every one is
#   `BRepGraph_RefId::Typed<Kind::X>`, a plain value struct wrapping a
#   uint32_t index, never a pointer.
#
# This is a name-based allowlist, not derived resolution — if OCCT ever adds
# a differently-shaped BaseTraits/RefTraits (or a member that's actually a
# pointer), this would silently misclassify it. Revisit if that ever matters.
_KNOWN_SAFE_UNREADABLE_TYPES = {"BaseTraits", "RefTraits"}

# Cursor kinds that are pure template-parameter syntax, not body content.
# A bare forward declaration like `template <typename T> struct RefTraits;`
# still carries a TEMPLATE_TYPE_PARAMETER child for `T` even though it has
# no body at all — so "no readable body" can't mean "zero children", or
# RefTraits's own unreadable-but-safe forward decl would never match.
_TEMPLATE_PARAMETER_KINDS = (
  clang.cindex.CursorKind.TEMPLATE_TYPE_PARAMETER,
  clang.cindex.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
  clang.cindex.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
)


def _has_no_readable_body(children):
  """True if `children` contains nothing but template-parameter syntax —
  i.e. `cls` is either truly childless or is only a forward declaration's
  parameter list, with no actual members, base specifiers, or anything else
  libclang could have used to answer a member lookup.
  """
  return all(c.kind in _TEMPLATE_PARAMETER_KINDS for c in children)


class _AssumedRecordType:
  """Stand-in for a `clang.cindex.Type` we can't actually reach (see
  `_KNOWN_SAFE_UNREADABLE_TYPES`), duck-typed just enough for
  `classify_return_cleanability` to land on its ordinary `RECORD -> CLEANABLE`
  branch — not a special case bolted onto the classifier itself.
  """

  kind = clang.cindex.TypeKind.RECORD
  spelling = "<assumed record via known-safe-but-unreadable base>"

  def get_canonical(self):
    return self


def _to_definition_cursor(cursor):
  """Normalize `cursor` to the cursor that actually carries its body (base
  specifiers, member typedefs, etc.), not a non-defining reference/forward
  declaration. `Type.get_declaration()` and `Cursor.get_definition()` can
  both hand back a cursor whose `get_children()` is empty even though a
  real definition exists elsewhere in the same translation unit — this is
  what silently starved the base-class walk of a `ChildId` typedef that
  genuinely is there. Returns `cursor` unchanged if it's already a
  definition, has none, or doesn't support the check at all (e.g. test
  mocks — always a no-op for them, never a regression there).
  """
  if cursor is None:
    return None
  if hasattr(cursor, "get_definition"):
    definition = cursor.get_definition()
    if definition is not None and definition.spelling:
      return definition
  return cursor


def _find_member_typedef(class_decl, member_name, classDict=None):
  """Search `class_decl`'s children, and recursively its base classes, for a
  TYPEDEF_DECL/TYPE_ALIAS_DECL named `member_name`. Returns its underlying
  `Type`, or `None`.

  OCCT traits structs commonly declare their member types once on a shared
  base and inherit them rather than redeclaring — e.g. BRepGraph_DefsIterator's
  `ShellOfSolidTraits : public BaseTraits<...>` gets `ChildId` from
  `BaseTraits`, not from its own direct children. Mirrors the base-class BFS
  in `resolver/strategies/nested.py::resolve_qualified_member_type`'s
  `tuInfo.classDict` fallback path (same shape, different lookup root).

  `classDict` (the binder's `tuInfo.classDict`, a name-keyed registry built
  by an earlier, separate TU pass) is an optional second source for a base
  class's cursor, queued *alongside* whatever `get_definition()`/
  `child.type.get_declaration()` produce — not instead of. Needed for bases
  that are themselves template instantiations (`: public BaseTraits<Args...>`):
  both direct AST APIs can agree on a `BaseTraits` cursor whose body isn't
  materialized (lazy instantiation), while the registry entry is.
  """
  visited = set()
  queue = [_to_definition_cursor(class_decl)]
  hit_known_safe_unreadable_type = False
  while queue:
    cls = queue.pop(0)
    cls_id = cls.spelling
    if cls_id in visited:
      continue
    visited.add(cls_id)
    children = list(cls.get_children())
    if cls_id in _KNOWN_SAFE_UNREADABLE_TYPES and _has_no_readable_body(children):
      # `cls` itself is a name verified safe by inspection (see
      # `_KNOWN_SAFE_UNREADABLE_TYPES`) but whose body libclang can't
      # materialize here. Checked at pop-time so it covers `cls` arriving
      # either as a base specifier (BaseTraits) or as this call's own root
      # (RefTraits, reached via `_resolve_dependent_member_type`'s
      # enclosing-class fallback below) uniformly.
      hit_known_safe_unreadable_type = True
    for child in children:
      if (
        child.kind in (clang.cindex.CursorKind.TYPEDEF_DECL, clang.cindex.CursorKind.TYPE_ALIAS_DECL)
        and child.spelling == member_name
      ):
        return child.underlying_typedef_type
      if child.kind == clang.cindex.CursorKind.CXX_BASE_SPECIFIER:
        base_decl = child.get_definition()
        if base_decl is None or not base_decl.spelling:
          # get_definition() doesn't reliably resolve a base specifier whose
          # type is itself a template instantiation (e.g. OCCT's traits
          # idiom, `: public BaseTraits<Args...>`) — fall back to resolving
          # through the base specifier's own `.type`, same technique already
          # used above to get from a template argument to its class cursor.
          base_decl = child.type.get_declaration() if hasattr(child, "type") else None
        if base_decl and base_decl.spelling:
          queue.append(_to_definition_cursor(base_decl))
          if classDict:
            from_dict = classDict.get(base_decl.spelling)
            if from_dict is not None:
              queue.append(_to_definition_cursor(from_dict))
  if hit_known_safe_unreadable_type:
    return _AssumedRecordType()
  return None


def _resolve_dependent_member_type(t, templateArgs, classDict=None):
  """Best-effort resolution of a dependent member type — e.g. a method
  declared to return `ParentId` where `using ParentId = typename TraitsT::ParentId;`
  inside a class template, and `TraitsT` is a template parameter substituted
  via `templateArgs` at this call site (see BRepGraph_ReverseIterator's
  `EdgeParentsOf<TraitsT>` — the smoking-gun case this exists for, same one
  `resolver/strategies/nested.py::resolve_qualified_member_type` handles for
  the TS-string path).

  Peels one typedef alias (`t` itself), substitutes its dependent parent
  name via `templateArgs`, and looks up the named member on the substituted
  class. Returns the resolved `Type`, or `None` if any step doesn't apply —
  callers should fall back to their normal "unknown" handling in that case.

  `parent_name` isn't always a template parameter, though — e.g.
  BRepGraph_RefsIterator's `RefIterator<RefType, TheFullTraverse>` declares
  `using Traits = Detail::RefTraits<RefType>;` then
  `using RefId = typename Traits::RefId;`: `Traits` is a *local member
  alias* of the enclosing class, not a template parameter, so it's never a
  key in `templateArgs`. `classDict` can't help resolve it either — it only
  indexes `CLASS_DECL`/`STRUCT_DECL` cursors (see `ast/walker.py::classDict`),
  never `TYPE_ALIAS_DECL`/`TYPEDEF_DECL` or `CLASS_TEMPLATE`. When
  `parent_name` isn't in `templateArgs`, fall back to looking it up as a
  sibling member typedef on `t`'s own enclosing class instead, then continue
  the member lookup on whatever *that* resolves to.
  """
  if not templateArgs:
    return None

  decl = t.get_declaration()
  if decl is None or decl.kind not in (
    clang.cindex.CursorKind.TYPEDEF_DECL,
    clang.cindex.CursorKind.TYPE_ALIAS_DECL,
  ):
    return None

  # `t`'s own underlying type, as written at the alias declaration — this
  # preserves the *named* template parameter (e.g. "TraitsT"), unlike
  # `t.get_canonical()` which collapses it to the unusable placeholder form
  # ("type-parameter-0-0").
  spelling = decl.underlying_typedef_type.spelling
  spelling = spelling.replace("typename ", "").replace("const ", "")
  spelling = spelling.replace("&", "").replace("*", "").strip()
  if "::" not in spelling:
    return None
  parent_name, member_name = (part.strip() for part in spelling.rsplit("::", 1))
  if not parent_name or not member_name:
    return None

  substituted_arg = templateArgs.get(parent_name)
  if substituted_arg is not None:
    substituted_type = getattr(substituted_arg, "type", substituted_arg)
    substituted_decl = substituted_type.get_declaration() if hasattr(substituted_type, "get_declaration") else None
    if not substituted_decl:
      return None
    return _find_member_typedef(substituted_decl, member_name, classDict)

  # `parent_name` isn't a template parameter at all — try it as a sibling
  # member typedef of the enclosing class instead (see docstring).
  enclosing_class = getattr(decl, "semantic_parent", None)
  if enclosing_class is None or not enclosing_class.spelling:
    return None
  parent_type = _find_member_typedef(enclosing_class, parent_name, classDict)
  if parent_type is None:
    return None
  parent_decl = parent_type.get_declaration() if hasattr(parent_type, "get_declaration") else None
  if parent_decl is None or not parent_decl.spelling:
    return None

  return _find_member_typedef(parent_decl, member_name, classDict)


def _is_class_template_instantiation(t):
  """True if `t`'s spelling is `TemplateName<Args>` and `TemplateName`'s own
  declaration is a genuine class template (or an already-resolved class),
  never a type-alias template.

  A class template (`template <typename T> class Foo {...};`) instantiation
  is *always* a class — the language gives no way for `Foo<X>` to be
  anything else, regardless of which class template it is or whether `X`
  itself is still a dependent placeholder. An alias template
  (`template <typename T> using Foo = T*;`) is pure textual substitution and
  could expand to anything, so it's excluded here rather than assumed safe.

  Covers both shapes seen so far: the injected-class-name self-reference
  (`NCollection_UtfString::SubString()` returning bare `NCollection_UtfString`,
  i.e. the enclosing template applied to its own parameter — trivially a
  CLASS_TEMPLATE, since we're in the middle of binding it) and a method
  returning an instantiation of a *different*, sibling class template using
  the enclosing template's own parameter (`NCollection_LinearVector<TheItemType>::ToArray1()`
  returning `NCollection_Array1<TheItemType>`) — both are ordinary class
  template instantiations, not textual aliasing, so both resolve the same way.
  """
  if "<" not in t.spelling:
    return False
  decl = t.get_declaration()
  if decl is None:
    return False
  return decl.kind in (
    clang.cindex.CursorKind.CLASS_TEMPLATE,
    clang.cindex.CursorKind.CLASS_DECL,
    clang.cindex.CursorKind.STRUCT_DECL,
  )


def _diagnose_unresolved(t, templateArgs, classDict=None):
  """Best-effort diagnostic dump for the final "unknown ownership" raise.

  Gathers everything relevant to why neither `_is_class_template_instantiation`
  nor `_resolve_dependent_member_type` could resolve `t`, replaying each of
  their internal decision points inline, so a crash from a long real build
  run carries enough context to diagnose without needing a second run.
  Never raises itself — any failure while gathering is caught and noted
  inline rather than masking the original raise.
  """
  parts = [f"templateArgs keys={sorted(templateArgs.keys()) if templateArgs else None}"]
  try:
    decl = t.get_declaration()
    parts.append(
      f"t.get_declaration()=(kind={decl.kind if decl else None!r}, spelling={decl.spelling if decl else None!r})"
    )
    if decl is not None and decl.kind in (
      clang.cindex.CursorKind.TYPEDEF_DECL,
      clang.cindex.CursorKind.TYPE_ALIAS_DECL,
    ):
      underlying_spelling = decl.underlying_typedef_type.spelling
      parts.append(f"underlying_typedef_type.spelling={underlying_spelling!r}")
      cleaned = underlying_spelling.replace("typename ", "").replace("const ", "")
      cleaned = cleaned.replace("&", "").replace("*", "").strip()
      if "::" in cleaned:
        parent_name, member_name = (p.strip() for p in cleaned.rsplit("::", 1))
        parts.append(f"parsed parent_name={parent_name!r} member_name={member_name!r}")
        if templateArgs:
          in_args = parent_name in templateArgs
          parts.append(f"parent_name in templateArgs={in_args}")
          if in_args:
            substituted_arg = templateArgs.get(parent_name)
            substituted_type = getattr(substituted_arg, "type", substituted_arg)
            substituted_decl = (
              substituted_type.get_declaration() if hasattr(substituted_type, "get_declaration") else None
            )
            parts.append(
              f"substituted_decl=(kind={substituted_decl.kind if substituted_decl else None!r}, "
              f"spelling={substituted_decl.spelling if substituted_decl else None!r})"
            )
            if substituted_decl:
              normalized_decl = _to_definition_cursor(substituted_decl)
              parts.append(
                f"normalized (definition) decl=(kind={normalized_decl.kind!r}, spelling={normalized_decl.spelling!r}, "
                f"is same object as substituted_decl={normalized_decl is substituted_decl})"
              )
              direct_children_cursors = list(normalized_decl.get_children())
              direct_children = [(c.kind, c.spelling) for c in direct_children_cursors]
              parts.append(f"normalized decl's direct children (ALL kinds)={direct_children!r}")

              # Trace the one untested step: resolving each CXX_BASE_SPECIFIER
              # child to the actual base class body. Neither this base-decl
              # nor its own children are exposed by the real lookup below, and
              # that's exactly where the trail has gone cold twice now.
              for base_child in direct_children_cursors:
                if base_child.kind != clang.cindex.CursorKind.CXX_BASE_SPECIFIER:
                  continue
                parts.append(f"-- base specifier {base_child.spelling!r} --")
                via_get_definition = base_child.get_definition()
                parts.append(
                  "  get_definition()="
                  f"(kind={via_get_definition.kind if via_get_definition else None!r}, "
                  f"spelling={via_get_definition.spelling if via_get_definition else None!r})"
                )
                via_type_decl = base_child.type.get_declaration() if hasattr(base_child, "type") else None
                parts.append(
                  "  type.get_declaration()="
                  f"(kind={via_type_decl.kind if via_type_decl else None!r}, "
                  f"spelling={via_type_decl.spelling if via_type_decl else None!r})"
                )
                chosen = via_get_definition if (via_get_definition and via_get_definition.spelling) else via_type_decl
                if chosen:
                  chosen_normalized = _to_definition_cursor(chosen)
                  base_children = [(c.kind, c.spelling) for c in chosen_normalized.get_children()]
                  parts.append(
                    f"  chosen base decl normalized=(kind={chosen_normalized.kind!r}, spelling={chosen_normalized.spelling!r}); "
                    f"ITS direct children (ALL kinds)={base_children!r}"
                  )
                else:
                  parts.append("  (neither get_definition() nor type.get_declaration() gave anything usable)")
                if classDict:
                  key = chosen.spelling if chosen else base_child.spelling
                  from_dict = classDict.get(key)
                  if from_dict is None:
                    parts.append(f"  classDict.get({key!r})=None")
                  else:
                    from_dict_normalized = _to_definition_cursor(from_dict)
                    dict_children = [(c.kind, c.spelling) for c in from_dict_normalized.get_children()]
                    parts.append(
                      f"  classDict.get({key!r}) normalized=(kind={from_dict_normalized.kind!r}, "
                      f"spelling={from_dict_normalized.spelling!r}); ITS direct children (ALL kinds)={dict_children!r}"
                    )
                else:
                  parts.append("  (no classDict passed in)")

              # Call the REAL lookup (including its base-class walk), not a
              # hand-copy — a hand-copy here previously went stale the moment
              # `_find_member_typedef` grew the base-class walk, showing "not
              # found" even after that fix started working.
              found = _find_member_typedef(substituted_decl, member_name, classDict)
              if found is None:
                parts.append(f"_find_member_typedef(including base classes)={member_name!r} -> not found")
              else:
                found_canonical = found.get_canonical()
                parts.append(
                  f"_find_member_typedef(including base classes)={member_name!r} -> found "
                  f"(spelling={found.spelling!r}, canonical={found_canonical.spelling!r}, kind={found_canonical.kind!r})"
                )
                if found_canonical.kind == clang.cindex.TypeKind.UNEXPOSED:
                  parts.append(
                    "(found type is ITSELF still dependent/UNEXPOSED — recursion onto it is what will "
                    "actually determine success or the next raise, not this lookup alone)"
                  )
          else:
            # parent_name isn't a template parameter — trace the sibling
            # member typedef fallback (see `_resolve_dependent_member_type`'s
            # docstring, the `Traits`/`RefTraits` case).
            enclosing_class = getattr(decl, "semantic_parent", None)
            parts.append(
              f"enclosing_class (decl.semantic_parent)=(kind={enclosing_class.kind if enclosing_class else None!r}, "
              f"spelling={enclosing_class.spelling if enclosing_class else None!r})"
            )
            if enclosing_class is not None and enclosing_class.spelling:
              parent_type = _find_member_typedef(enclosing_class, parent_name, classDict)
              if parent_type is None:
                parts.append(f"_find_member_typedef(enclosing_class, {parent_name!r}) -> not found")
              else:
                parent_type_canonical = parent_type.get_canonical()
                parts.append(
                  f"_find_member_typedef(enclosing_class, {parent_name!r}) -> found "
                  f"(spelling={parent_type.spelling!r}, canonical={parent_type_canonical.spelling!r})"
                )
                parent_decl = parent_type.get_declaration() if hasattr(parent_type, "get_declaration") else None
                parts.append(
                  f"parent_type.get_declaration()=(kind={parent_decl.kind if parent_decl else None!r}, "
                  f"spelling={parent_decl.spelling if parent_decl else None!r})"
                )
                if parent_decl is not None and parent_decl.spelling:
                  found_via_parent = _find_member_typedef(parent_decl, member_name, classDict)
                  if found_via_parent is None:
                    parts.append(f"_find_member_typedef(parent_decl, {member_name!r}) -> not found")
                  else:
                    found_via_parent_canonical = found_via_parent.get_canonical()
                    parts.append(
                      f"_find_member_typedef(parent_decl, {member_name!r}) -> found "
                      f"(spelling={found_via_parent.spelling!r}, canonical={found_via_parent_canonical.spelling!r}, "
                      f"kind={found_via_parent_canonical.kind!r})"
                    )
      else:
        parts.append("(no '::' in underlying spelling after stripping qualifiers)")
  except Exception as diag_exc:
    parts.append(f"(diagnostic gathering itself failed: {diag_exc!r})")
  return "; ".join(parts)


def classify_return_cleanability(t, templateArgs=None, classDict=None):
  ct = t.get_canonical()

  # char*/const char* returns are wire-marshaled by OCJS's own codegen as
  # std::string (bindings.py's returnNeedsCStringWrapper), which embind
  # marshals as a plain JS string — no owned wrapper, nothing to leak.
  # Must be checked before the POINTER case below, since c-strings are
  # themselves POINTER-kind at the canonical level.
  if isCString(t):
    return ReturnCleanability.EITHER

  # Raw pointers are the one known unsafe case.
  if ct.kind == clang.cindex.TypeKind.POINTER:
    return ReturnCleanability.NON_CLEANABLE

  # Embind's default treatment of C++ references is to copy the value
  # into an owned JS wrapper. Reference-to-array is unsupported for now —
  # raise so we notice and decide deliberately.
  if ct.kind in (
    clang.cindex.TypeKind.LVALUEREFERENCE,
    clang.cindex.TypeKind.RVALUEREFERENCE,
  ):
    pointee = ct.get_pointee().get_canonical()
    # Foo*& (e.g. OCCT's `const Xxx_PYyy&` internal-pointer-member
    # accessors): bindings.py's needsCStringOrLvalueWrapper already routes
    # this exact shape through emscripten::val(ret, allow_raw_pointers())
    # — `auto ret = ...` copies the pointer *value*, so the wire result is
    # the same non-owning aliased handle a bare Foo* return already is.
    # Same classification as bare pointer, not a raise.
    if pointee.kind == clang.cindex.TypeKind.POINTER:
      return ReturnCleanability.NON_CLEANABLE
    if pointee.kind in (
      clang.cindex.TypeKind.CONSTANTARRAY,
      clang.cindex.TypeKind.INCOMPLETEARRAY,
      clang.cindex.TypeKind.VARIABLEARRAY,
      clang.cindex.TypeKind.DEPENDENTSIZEDARRAY,
    ):
      _raise(
        f"Unsupported reference return {t.spelling!r}: canonical={ct.spelling!r}, pointee kind={pointee.kind} arr{templateArgs.keys() if templateArgs else None}"
      )
    return ReturnCleanability.CLEANABLE

  # No C++ object exists that the finalizer could accidentally destroy.
  if ct.spelling in primitive_types:
    return ReturnCleanability.EITHER

  # OCJS always registers enums via emscripten::enum_value_type::string
  # (embind/enum.py) -> plain JS string, no delete(). If that ever changes
  # to the object/number variant, this silently goes stale — revisit then.
  if ct.kind == clang.cindex.TypeKind.ENUM:
    return ReturnCleanability.EITHER

  # Ordinary class/struct values are owned by the returned Embind wrapper.
  if ct.kind == clang.cindex.TypeKind.RECORD:
    return ReturnCleanability.CLEANABLE

  # Last resort before giving up — `t` may be a dependent type inside a
  # class template that a raw `get_canonical()` can't see through. Four
  # shapes handled, in order:

  # `t` may be a bare, un-aliased reference to the enclosing template's own
  # parameter — e.g. `template <typename ContainerT, typename TypedIdT>
  # class Foo { TypedIdT Method(); };` returning `TypedIdT` directly, no
  # `typename X::Y` indirection at all, so `t.get_declaration()` is
  # NO_DECL_FOUND rather than a typedef `_resolve_dependent_member_type`
  # could peel. If `t.spelling` is itself a key in `templateArgs`, it's
  # naming the parameter, not a member of it — substitute directly.
  if templateArgs and t.spelling in templateArgs:
    substituted_arg = templateArgs[t.spelling]
    substituted_type = getattr(substituted_arg, "type", substituted_arg)
    return classify_return_cleanability(substituted_type, templateArgs, classDict)

  # Checked against both `t` and its canonical `ct`: `t` catches a class
  # template instantiation used directly as a return type (no alias, e.g.
  # `NCollection_LinearVector::ToArray1()` returning `NCollection_Array1<...>`
  # bare); `ct` catches one reached *through* an alias whose own name has no
  # `<` (e.g. BRepGraph_ReverseIterator's `LookupParentRefsOf<TraitsT>`
  # declaring `using ResultType = ParentRef<ParentIdType, RefIdType>;` —
  # `t.spelling` is just `"ResultType"`, but `t.get_canonical()` fully
  # expands through the alias to `ParentRef<typename TraitsT::ParentId, ...>`,
  # which does have the `TemplateName<Args>` shape). `ParentRef` itself is an
  # ordinary, fully-readable class template (BRepGraph_ReverseIterator.hxx:
  # `struct ParentRef { ParentIdT ParentId; RefIdT Ref; };`, two value
  # fields) — no lazy-instantiation/allowlist concern here at all.
  if _is_class_template_instantiation(t) or _is_class_template_instantiation(ct):
    return ReturnCleanability.CLEANABLE

  resolved = _resolve_dependent_member_type(t, templateArgs, classDict)
  if resolved is not None:
    return classify_return_cleanability(resolved, templateArgs, classDict)

  _raise(
    f"Unknown return ownership for {t.spelling!r}: canonical={ct.spelling!r}, kind={ct.kind}. "
    f"Diagnostics: {_diagnose_unresolved(t, templateArgs, classDict)}"
  )


def assert_no_non_cleanable_fields(types, templateArgs=None, classDict=None):
  """Raise if any of `types` classifies as NON_CLEANABLE (a raw pointer).

  Used to validate every field type that ends up in an RBV envelope — the
  primary return field and every output-param field, including fields
  inherited from a base override's own output params. NON_CLEANABLE is the
  only disallowed bucket; CLEANABLE and EITHER are both fine. Also raises
  (via `classify_return_cleanability`) for any field type that's itself
  unclassifiable, e.g. `Foo*&`.
  """
  for t in types:
    if classify_return_cleanability(t, templateArgs, classDict) == ReturnCleanability.NON_CLEANABLE:
      _raise(f"RBV envelope field is not cleanable (raw pointer): {t.spelling!r}")


def any_cleanable(types, templateArgs=None, classDict=None):
  """True if any of `types` classifies as CLEANABLE.

  Used to decide whether an RBV envelope needs to be wrapped in `Leaking<T>`
  as a whole — the envelope wraps iff at least one field it contains
  (primary return field, own output-param fields, or base-inherited output
  fields) is itself a cleanable value.
  """
  return any(classify_return_cleanability(t, templateArgs, classDict) == ReturnCleanability.CLEANABLE for t in types)
