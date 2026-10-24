/**
 * Require every `new`-constructed instance of a bound OCCT class, and every
 * `Leaking<T>`-returning call, to be passed as the direct argument to
 * `fr(...)` (`fr<T>(x: Leaking<T>): T`, see `MemSafety Design Doc.md`).
 * Construction always transfers ownership; `Leaking<T>` marks returns
 * `classify_return_cleanability` classified the same way. `fr()` is the one
 * place that registers a handle for finalization tracking — anything else
 * never reaches it.
 */
import type { Rule } from 'eslint';
import type { CallExpression, Node as EstreeNode, NewExpression } from 'estree';
import type * as ts from 'typescript';

// Matches both the raw per-variant output (`opencascade_single.d.ts`,
// `opencascade_multi.d.ts`, used inside this repo) and `types.d.ts`, where
// `libcascade assemble` merges shared declarations for the published
// package — consumers only ever see the latter.
const BINDINGS_DECLARATION_FILE = /(?:opencascade_(?:single|multi)|types)\.d\.ts$/;

const FR_WRAPPER_NAME = 'fr';

type NodeWithParent = EstreeNode & { parent?: EstreeNode };

/**
 * Walk past casts/assertions that don't change runtime identity, to find
 * the expression's true syntactic parent for wrapper-check purposes (e.g.
 * `fr(new oc.Foo() as Foo)`).
 */
function unwrapCasts(node: NodeWithParent): EstreeNode {
  let current: NodeWithParent = node;
  while (current.parent) {
    // TS-specific node types (`TSAsExpression`, etc.) aren't part of plain
    // `estree`'s `Node.type` union, so this widens through `unknown` rather
    // than intersecting — an intersection would keep the narrower union and
    // make the comparisons below a compile error.
    const parent = current.parent as unknown as { type: string; expression?: EstreeNode };
    if (
      (parent.type === 'TSAsExpression' ||
        parent.type === 'TSTypeAssertion' ||
        parent.type === 'TSSatisfiesExpression' ||
        parent.type === 'TSNonNullExpression') &&
      parent.expression === current
    ) {
      current = parent as unknown as NodeWithParent;
      continue;
    }
    break;
  }
  return current;
}

/**
 * True iff `node`'s (unwrapped) syntactic parent is a call to the `fr(...)`
 * wrapper with `node` as its sole argument.
 */
function isWrappedByFr(node: NodeWithParent): boolean {
  const outer = unwrapCasts(node);
  const parent = (outer as NodeWithParent).parent as
    | (EstreeNode & {
        type: string;
        callee?: EstreeNode & { type?: string; name?: string };
        arguments?: EstreeNode[];
      })
    | undefined;
  if (!parent || parent.type !== 'CallExpression') {
    return false;
  }
  return (
    parent.callee?.type === 'Identifier' && parent.callee.name === FR_WRAPPER_NAME && parent.arguments?.[0] === outer
  );
}

/**
 * True iff `symbol`'s declarations all live in the generated bindings
 * `.d.ts` — i.e. it's "from this .d.ts", with no per-symbol marking
 * required. Used both for class symbols (the `new X()` check) and for the
 * `Leaking` alias symbol itself, so an unrelated same-named `Leaking<T>`
 * declared elsewhere in the program can't be mistaken for ours.
 */
function isDeclaredInBindingsDts(symbol: ts.Symbol | undefined): boolean {
  const declarations = symbol?.getDeclarations?.() ?? symbol?.declarations;
  if (!declarations || declarations.length === 0) {
    return false;
  }
  return declarations.some((declaration) => BINDINGS_DECLARATION_FILE.test(declaration.getSourceFile().fileName));
}

/**
 * True iff `type` is declared in the generated bindings `.d.ts` — i.e. it's
 * one of "all classes from this .d.ts", with no per-class marking required.
 */
function isFromBindingsDts(type: ts.Type): boolean {
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
 */
function isLeakingType(type: ts.Type): boolean {
  return type.aliasSymbol?.getName() === 'Leaking' && isDeclaredInBindingsDts(type.aliasSymbol);
}

/**
 * `parserServices` is set up by typescript-eslint when
 * `parserOptions.project` is configured. Shape isn't part of `@types/eslint`
 * — typescript-eslint attaches it dynamically to the rule context.
 */
type ParserServicesLike = {
  program?: ts.Program;
  esTreeNodeToTSNodeMap?: Map<unknown, ts.Node>;
};

export const requireFrWrapperRule: Rule.RuleModule = {
  meta: {
    type: 'problem',
    docs: {
      description: `Require constructed bindings instances and Leaking<T> returns to be passed through ${FR_WRAPPER_NAME}(...) before use.`,
    },
    messages: {
      missingFrConstruct:
        '`new {{typeText}}(...)` must be wrapped: `fr(new {{typeText}}(...))`. Construction always transfers ownership to the caller.',
      missingFrLeaking: 'This call returns `{{typeText}}`, which must be passed through `fr(...)`.',
    },
    schema: [],
  },
  create(context) {
    // Fall back to a no-op if the consumer hasn't enabled type-aware linting.
    const contextWithServices = context as Rule.RuleContext & {
      sourceCode?: { parserServices?: ParserServicesLike };
      parserServices?: ParserServicesLike;
    };
    const services = contextWithServices.sourceCode?.parserServices ?? contextWithServices.parserServices;
    const { program, esTreeNodeToTSNodeMap } = services ?? {};
    if (!program || !esTreeNodeToTSNodeMap) {
      return {};
    }
    const checker = program.getTypeChecker();

    return {
      NewExpression(node: NewExpression) {
        const esNode = node as NodeWithParent;
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
          node: node as unknown as Rule.Node,
          messageId: 'missingFrConstruct',
          data: { typeText: checker.typeToString(type) },
        });
      },

      CallExpression(node: CallExpression) {
        const esNode = node as NodeWithParent;
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
          node: node as unknown as Rule.Node,
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
