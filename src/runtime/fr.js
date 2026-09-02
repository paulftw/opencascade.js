/**
 * Runtime companion to the `Leaking<T>` MemSafety brand (see
 * `MemSafety Design Doc.md`) — the one place a `Leaking<T>` value becomes a
 * usable `T`. Lint enforces this for every `new`-constructed instance and
 * `Leaking<T>`-typed result.
 *
 * Every embind instance reachable from `x` is wrapped in a transparent
 * `Proxy`, registered with a `FinalizationRegistry` holding the real
 * instance as `heldValue`. The proxy is what callers hold — reference only
 * runs proxy to real, so the registry can't keep the proxy reachable.
 *
 * `.delete()` / `[Symbol.dispose]()` on the proxy forwards to the real
 * instance's own `.delete()`, synchronously. If never disposed and the
 * proxy is later collected, the registry callback deletes the real
 * instance instead.
 *
 * @template T
 * @param {import('../../dist/opencascade_single.js').Leaking<T>} leaking
 * @returns {T}
 */
export function fr(leaking) {
  return /** @type {T} */ (wrap(leaking));
}

/**
 * @typedef {{
 *   delete: () => void,
 *   isDeleted: () => boolean,
 *   [Symbol.dispose]: () => void,
 *   constructor?: { name?: string },
 * }} EmbindInstance
 */

/**
 * @param {unknown} x
 * @returns {x is EmbindInstance}
 */
function isEmbindInstance(x) {
  return (
    typeof (/** @type {{ delete?: unknown }} */ (x).delete) === 'function' &&
    typeof (/** @type {{ [Symbol.dispose]?: unknown }} */ (x)[Symbol.dispose]) === 'function'
  );
}

/**
 * Best-effort human-readable class name for the leak warning. `constructor
 * .name` is used deliberately rather than reaching into `$$` — embind sets
 * it explicitly via `createNamedFunction` from the real C++-registered
 * class name, and unlike `$$`'s fields, `Function.prototype.name` is not
 * subject to Closure's property mangling.
 *
 * @param {EmbindInstance} x
 * @returns {string}
 */
function describeClass(x) {
  return x.constructor?.name ?? 'unknown';
}

/** @type {FinalizationRegistry<{ instance: EmbindInstance, stack?: string }>} */
const registry = new FinalizationRegistry((entry) => {
  if (entry.instance.isDeleted()) {
    return; // Disposed properly already — every proper disposal unregisters, so this shouldn't fire, but stay defensive.
  }
  entry.instance.delete();
});

/**
 * Wrap one real embind instance in a transparent proxy, tracked for
 * backstop auto-disposal.
 *
 * @param {EmbindInstance} x
 * @returns {EmbindInstance}
 */
function makeTrackingProxy(x) {
  /** @type {EmbindInstance | null} */
  let live = x;

  const proxy = /** @type {EmbindInstance} */ (
    new Proxy(/** @type {object} */ (x), {
      get(_target, prop) {
        if (live === null) {
          throw new Error(`${describeClass(x)}: instance already deleted`);
        }
        // Intercepted directly rather than reaching into embind's `$$`
        // record: `$$` is NOT minification-safe (`dist/opencascade_single.js`
        // renames it to `mb`), but `.delete()`/`[Symbol.dispose]` survive —
        // embind writes them with quoted keys, which Closure's property
        // renamer treats as protected.
        if (prop === 'delete' || prop === Symbol.dispose) {
          return () => {
            if (live === null) {
              throw new Error(`${describeClass(x)}: instance already deleted`);
            }
            const real = live;
            live = null; // Wipe the pointer to the proxied instance first — any reentrant access sees the dead state.
            registry.unregister(proxy);
            real.delete();
          };
        }
        const value = Reflect.get(live, prop, live);
        return typeof value === 'function' ? value.bind(live) : value;
      },
      set(_target, prop, value) {
        if (live === null) {
          throw new Error(`${describeClass(x)}: instance already deleted`);
        }
        return Reflect.set(live, prop, value, live);
      },
    })
  );

  registry.register(proxy, { instance: x, stack: new Error().stack }, proxy);
  return proxy;
}

/**
 * Recursively wrap every real embind instance reachable from `x`, replacing
 * it with its tracking proxy. Three shapes reach here: a lone embind
 * instance (`Leaking<Foo>`), an RBV envelope (`Leaking<{ returnValue, S,
 * [Symbol.dispose] }>`), or a tuple from a `std::pair`/`std::array` return
 * (`Leaking<[A, B]>`) — including nested combinations. Dispatch is on
 * runtime shape, not the codegen call site's static type, since both
 * collapse to the same handful of shapes.
 *
 * A plain-object envelope's own `[Symbol.dispose]` (the `using`-oriented
 * `__ocjsRbvDispose__` disposer, see `embind_builtins.py`) is deliberately
 * dropped, not carried into the rebuilt object — a different, scope-based
 * disposal mechanism; each field is independently tracked via its own proxy
 * instead.
 *
 * @param {unknown} x
 * @returns {unknown}
 */
function wrap(x) {
  if (x === null || x === undefined || typeof x !== 'object') {
    return x; // primitive (also handles `typeof null === 'object'` via the explicit null check above)
  }
  if (isEmbindInstance(x)) {
    return makeTrackingProxy(x);
  }
  if (Array.isArray(x)) {
    return x.map(wrap);
  }
  /** @type {Record<string, unknown>} */
  const result = {};
  for (const key of Object.keys(x)) {
    result[key] = wrap(/** @type {Record<string, unknown>} */ (x)[key]);
  }
  return result;
}
