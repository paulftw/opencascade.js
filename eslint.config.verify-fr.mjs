// Throwaway config to verify require-fr-wrapper's own logic against a real
// file, without touching the repo's real eslint.config.mjs.
import tseslint from 'typescript-eslint';
import ocjsLintPlugin from './tools/eslint-plugin/index.js';

export default tseslint.config({
  files: ['**/*.mjs'],
  languageOptions: {
    parser: tseslint.parser,
    parserOptions: {
      projectService: {
        allowDefaultProject: ['experiments/leaksafety/cylinder-cut-tessellation-memory.mjs'],
        defaultProject: 'tests/tsconfig.json',
      },
      tsconfigRootDir: import.meta.dirname,
    },
  },
  plugins: { 'ocjs-lint': ocjsLintPlugin },
  rules: {
    'ocjs-lint/require-fr-wrapper': 'error',
  },
});
