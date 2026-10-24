/**
 * Published ESLint plugin: `ocjs-memsafety`.
 *
 * Consumer-facing subset of the repo-internal `ocjs-lint` plugin — only the
 * `require-fr-wrapper` rule, which is coupled to the `Leaking<T>` brand this
 * package emits. Imported by the same rule module the internal config uses,
 * so there is one implementation, not a copy.
 */
import type { ESLint } from 'eslint';

import { requireFrWrapperRule } from './require-fr-wrapper.ts';

const plugin: ESLint.Plugin = {
  meta: {
    name: 'ocjs-memsafety',
    version: '1.0.0',
  },
  rules: {
    'require-fr-wrapper': requireFrWrapperRule,
  },
};

export default plugin;
