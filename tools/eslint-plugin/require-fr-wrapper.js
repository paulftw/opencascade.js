/**
 * Require every `new`-constructed instance of a bound OCCT class, and every
 * `Leaking<T>`-returning call, to be passed as the direct argument to
 * `fr(...)` (`fr<T>(x: Leaking<T>): T`, see `MemSafety Design Doc.md`).
 * Construction always transfers ownership; `Leaking<T>` marks returns
 * `classify_return_cleanability` classified the same way. `fr()` is the one
 * place that registers a handle for finalization tracking — anything else
 * never reaches it.
 *
 * @typedef {import('eslint').Rule.RuleModule} RuleModule
 * @typedef {import('estree').Node} EstreeNode
 * @typedef {import('estree').NewExpression} NewExpression
 * @typedef {import('estree').CallExpression} CallExpression
 */

const BINDINGS_DECLARATION_FILE = /opencascade_single\.d\.ts$/;

const FR_WRAPPER_NAME = 'fr';

/**
 * Walk past casts/assertions that don't change runtime identity, to find
 * the expression's true syntactic parent for wrapper-check purposes (e.g.
 * `fr(new oc.Foo() as Foo)`).
 *
 * @param {EstreeNode & { parent?: EstreeNode }} node
 * @returns {EstreeNode}
 */
function unwrapCasts(node) {
  let current = node;
  while (current.parent) {
    const { parent } = /** @type {{ parent: EstreeNode & { type: string; expression?: EstreeNode } }} */ (
      /** @type {unknown} */ (current)
    );
    if (
      (parent.type === 'TSAsExpression' ||
        parent.type === 'TSTypeAssertion' ||
        parent.type === 'TSSatisfiesExpression' ||
        parent.type === 'TSNonNullExpression') &&
      parent.expression === current
    ) {
      current = /** @type {EstreeNode & { parent?: EstreeNode }} */ (parent);
      continue;
    }
    break;
  }
  return current;
}

/**
 * True iff `node`'s (unwrapped) syntactic parent is a call to the `fr(...)`
 * wrapper with `node` as its sole argument.
 *
 * @param {EstreeNode & { parent?: EstreeNode }} node
 * @returns {boolean}
 */
function isWrappedByFr(node) {
  const outer = unwrapCasts(node);
  const { parent } = /** @type {{ parent?: EstreeNode & {
    type: string;
    callee?: EstreeNode & { type?: string; name?: string };
    arguments?: EstreeNode[];
  } }} */ (/** @type {unknown} */ (outer));
  if (!parent || parent.type !== 'CallExpression') {
    return false;
  }
  return (
    parent.callee?.type === 'Identifier' &&
    parent.callee.name === FR_WRAPPER_NAME &&
    parent.arguments?.[0] === outer
  );
}

/**
 * True iff `symbol`'s declarations all live in the generated bindings
 * `.d.ts` — i.e. it's "from this .d.ts", with no per-symbol marking
 * required. Used both for class symbols (the `new X()` check) and for the
 * `Leaking` alias symbol itself, so an unrelated same-named `Leaking<T>`
 * declared elsewhere in the program can't be mistaken for ours.
 *
 * @param {import('typescript').Symbol | undefined} symbol
 * @returns {boolean}
 */
function isDeclaredInBindingsDts(symbol) {
  const declarations = symbol?.getDeclarations?.() ?? symbol?.declarations;
  if (!declarations || declarations.length === 0) {
    return false;
  }
  return declarations.some((declaration) => BINDINGS_DECLARATION_FILE.test(declaration.getSourceFile().fileName));
}

/**
 * True iff `type` is declared in the generated bindings `.d.ts` — i.e. it's
 * one of "all classes from this .d.ts", with no per-class marking required.
 *
 * @param {import('typescript').Type} type
 * @returns {boolean}
 */
function isFromBindingsDts(type) {
  return isDeclaredInBindingsDts(type.getSymbol());
}

/**
 * True iff `type` is a reference to *the* `Leaking<T>` type alias declared
 * in the bindings `.d.ts` — checked via `aliasSymbol`, not `getSymbol()`,
 * since `Leaking<T>` is a type *alias* over an anonymous object shape
 * (`{ readonly __leaking: T }`); the alias's own name and declaration only
 * show up on `aliasSymbol`. Matching the declaration site, not just the
 * name `"Leaking"`, avoids collision with an unrelated same-named alias
 * declared elsewhere in the program.
 *
 * @param {import('typescript').Type} type
 * @returns {boolean}
 */
function isLeakingType(type) {
  return type.aliasSymbol?.getName() === 'Leaking' && isDeclaredInBindingsDts(type.aliasSymbol);
}

/** @type {RuleModule} */
export const requireFrWrapperRule = {
  meta: {
    type: 'problem',
    docs: {
      description: `Require constructed bindings instances and Leaking<T> returns to be passed through ${FR_WRAPPER_NAME}(...) before use.`,
    },
    messages: {
      missingFrConstruct:
        '`new {{typeText}}(...)` must be wrapped: `fr(new {{typeText}}(...))`. Construction always transfers ownership to the caller.',
      missingFrLeaking:
        'This call returns `{{typeText}}`, which must be passed through `fr(...)`.',
    },
    schema: [],
  },
  create(context) {
    // `parserServices` is set up by typescript-eslint when
    // `parserOptions.project` is configured. Fall back to a no-op if the
    // consumer hasn't enabled type-aware linting.
    const services =
      /** @type {{ sourceCode?: { parserServices?: unknown }; parserServices?: unknown }} */ (
        /** @type {unknown} */ (context)
      ).sourceCode?.parserServices ??
      /** @type {{ parserServices?: unknown }} */ (/** @type {unknown} */ (context)).parserServices;
    const { program, esTreeNodeToTSNodeMap } =
      /** @type {{ program?: import('typescript').Program; esTreeNodeToTSNodeMap?: Map<unknown, import('typescript').Node> }} */ (
        services ?? {}
      );
    if (!program || !esTreeNodeToTSNodeMap) {
      return {};
    }
    const checker = program.getTypeChecker();

    return {
      /** @param {NewExpression} node */
      NewExpression(node) {
        const esNode = /** @type {EstreeNode & { parent?: EstreeNode }} */ (/** @type {unknown} */ (node));
        if (isWrappedByFr(esNode)) {
          return;
        }
        const tsNode = esTreeNodeToTSNodeMap.get(node);
        if (!tsNode) {
          return;
        }
        const type = checker.getTypeAtLocation(tsNode);
        if (!isFromBindingsDts(type)) {
          return;
        }
        context.report({
          node: /** @type {import('eslint').Rule.Node} */ (/** @type {unknown} */ (node)),
          messageId: 'missingFrConstruct',
          data: { typeText: checker.typeToString(type) },
        });
      },

      /** @param {CallExpression} node */
      CallExpression(node) {
        const esNode = /** @type {EstreeNode & { parent?: EstreeNode }} */ (/** @type {unknown} */ (node));
        if (isWrappedByFr(esNode)) {
          return;
        }
        const tsNode = esTreeNodeToTSNodeMap.get(node);
        if (!tsNode) {
          return;
        }
        const type = checker.getTypeAtLocation(tsNode);
        if (!isLeakingType(type)) {
          return;
        }
        const innerType = type.aliasTypeArguments?.[0];
        context.report({
          node: /** @type {import('eslint').Rule.Node} */ (/** @type {unknown} */ (node)),
          messageId: 'missingFrLeaking',
          data: {
            typeText: checker.typeToString(type),
            innerText: innerType ? checker.typeToString(innerType) : 'T',
          },
        });
      },
    };
  },
};

/**
 * Default export — registry-friendly shape for plugin authors who want
 * to reference the rule by camelCase or by file basename.
 */
export default requireFrWrapperRule;
