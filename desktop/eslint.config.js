import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import eslintConfigPrettier from "eslint-config-prettier/flat";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist/", "src-tauri/"]),
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    extends: [tseslint.configs.recommendedTypeChecked, reactHooks.configs.flat.recommended],
    languageOptions: {
      parserOptions: {
        // tsconfig.json covers src only; vite.config.ts is typed by tsconfig.node.json.
        projectService: {
          allowDefaultProject: ["vite.config.ts"],
          defaultProject: "tsconfig.node.json",
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["*.{js,ts}"],
    languageOptions: { globals: globals.node },
  },
  eslintConfigPrettier,
]);
