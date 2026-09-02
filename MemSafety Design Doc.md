MemSafety feature.

overall this is a fork of https://github.com/donalffons/opencascade.js/pull/301

Goal:

Add extra memory leak checks at build/compile time of client JS programs.

When a method or other ocjs library function returns shit that needs
cleaning up its call must instead of original return type `Foo` be declared as  `Leaking<Foo>`

client (via lint check or something) will have to wrap such calls into 
`fr(oc.someMethod(x,y,z))` `fr` will do something, none of your business what and return original.
all we need to know is that it's signature is
`fr(Leaking<T>): T`


we will do it via typescript .d.ts


"shit needing cleanup" is Foo&, Foo.

primitive types and undefined can be considered cleanable and not
cleanable. client will be aware and will handle false positives 
for these returns as they can be unambiguously distinguished.

So pieces we need to build:
1. confirm that result_marker.py filter is correctly 
distinguishing cleanable and non-cleanable stuff. 
it doesn't have to cover all possible C++ types only what is already 
produced by OCCT bindings is required. so if it's not in OCJS just fucking raise so we will catch and fix not previously supported case
when future version introduces it.

2. find a bottleneck where after a group of methods is combined into
one TS function declaration we check that all return types are either
simultaneously cleanable or uncleanable (ignoring primitives).
if this is not the case - raise and crash whole build

3. find a bottleneck to check that all shit that RBV converts into return fields is either primitive or cleanable. and original return type is also cleanable or primitive.

if that's not the case - raise and crash whole build.

4. insert TS preamble if needed for the `Leaking<T>` template or whatever.

5. augment all method declarations output, probably *after* RBV synthesized the actual return type. if it is RBV or cleanable --
spit `theMethod(...whatever...): Leaking<T>;`

## Implementation checklist

- [X] **1. Verify `result_marker.py` classification.** Shipped as `classify_return_cleanability(t) -> ReturnCleanability` (`CLEANABLE` / `NON_CLEANABLE` / `EITHER`), not a bool. `Foo*&` and `Foo[]&` raise (unsupported, deliberately unhandled for now). `ENUM -> EITHER`, tied to `embind/enum.py` hardcoding `enum_value_type::string`. Verify: `tests/unit/test_result_marker.py`, 12 cases incl. a tripwire that fails if `embind/enum.py` stops hardcoding the string variant. Full-build verification (every bindable method's real result type) still pending — not yet run in this environment (missing vendored LLVM 17 deps).

- [X] **2. Re-add the overload-group mixed-return raise.** In `processMethodGroup` (`bindings.py:4179-4193`), after the rvalue-ref filter and before `_rbv.select_js_effective_overload_survivors`: compute `{ classify_return_cleanability(m.result_type) for m in bindable } - {EITHER}`, raise plain `Exception` (not `SkipException` — that call site swallows `SkipException` and would silently continue) with the full list of `ReturnType Class::method(args)` signatures if more than 1 distinct value remains. Verify so far: `ast.parse` syntax check only. Full-build verification still pending (same environment gap as step 1).

- [X] **3. Add RBV envelope field-cleanliness check.** In `_buildOutputParamReturnType` (`bindings.py:3881`), before each `return "{ ... }"`, check every emitted field: primary return field via `return_is_finalizer_safe(method.result_type)`, each output-param field via `rbv.output_arg_is_embind_managed(arg)` combined with `return_is_finalizer_safe(arg.type)` for non-managed non-primitive fields. Raise (class, method, field name, field C++ type) on anything that classifies as neither primitive nor cleanable. Verify: full build completes or raises naming the exact offending field.

- [X] **4. Declare `Leaking<T>` exactly once.** In `yaml_build.py`, right after `typescriptDefinitionOutput = ""` (~line 1119), append `export type Leaking<T> = { readonly __leaking: T };\n\n` (opaque brand — no member passthrough). Verify: `grep -c "type Leaking" <generated .d.ts>` == 1.

- [ ] **5. Wrap return types — two different decisions, not one.**

  **S0 (plain, non-RBV) case — per-method, at the 3 *final*-emission call sites only, never inside `getTypescriptDefFromResultType` itself** (that function also builds the inner `returnValue:` field text at `bindings.py:3925`/`3959` — wrapping there would double-wrap an envelope field). The 3 final sites are `_render_synthesized_base_signature` (`3654`), `processMethodOrProperty` (`4132`), `_ts_args_and_return` (`4259`). At each: after computing `returnType` from `getTypescriptDefFromResultType(method.result_type, ...)`, wrap it as `Leaking<{returnType}>` iff `classify_return_cleanability(method.result_type) == CLEANABLE` (skip on `EITHER`/`NON_CLEANABLE`). This is safe to decide per-method, with no group-wide lookup, *because* step 2 already raised earlier in the pipeline if the enclosing overload group were inconsistent — by the time we reach these sites that invariant is already guaranteed, not something step 5 has to re-derive. Still genuinely per-method, not per-group: one name-group can legally mix an `EITHER` overload (e.g. `void`) with a `CLEANABLE` one (step 2 ignores `EITHER` when checking), so each line's own type decides its own wrap, not a single group-wide verdict applied uniformly.

  **RBV envelope case — per-method, mandatory full-field check, no shortcuts.** In `_buildOutputParamReturnType`'s two `return "{ ... }"` sites, wrap the *whole* envelope as `Leaking<{ ... }>` iff **any** type in that method's own already-gathered step-3 field list (`[method.result_type] + outputArgs types`, plus `base_output` types in the mismatch branch) classifies `CLEANABLE` — reuse the exact list step 3 built, don't recompute a narrower one. This can't be simplified to "check the primary return field only" or assumed group-uniform: RBV envelope shape is driven by each method's own output params, which vary between overloads in the same name-group independently of whether their raw C++ return types passed step 2's check.

  Verify: regenerate `.d.ts` for `AppStd_Application` and `TDataXtd_Label` and diff against `dist/opencascade_single.d.ts`. Expect exactly:
    - `DynamicType(): Standard_Type;` → `DynamicType(): Leaking<Standard_Type>;`
    - `ResourcesName(): string;` → unchanged
    - `static Find(current: TDF_Label): { returnValue: boolean; S: TDataXtd_Shape; [Symbol.dispose](): void };` → `static Find(current: TDF_Label): Leaking<{ returnValue: boolean; S: TDataXtd_Shape; [Symbol.dispose](): void }>;`
    - plus a mixed-group case: a name-group with one `void`-returning overload and one `CLEANABLE`-returning overload emits `void` on the first line and `Leaking<...>` only on the second.
