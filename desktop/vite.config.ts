import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { searchForWorkspaceRoot } from "vite";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
    fs: {
      // Vite's default, and the worker's sources, whose messages ErrorBanner.test.tsx reads with `?raw`.
      allow: [searchForWorkspaceRoot(import.meta.dirname), "../worker/mathimator_worker"],
    },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: {
    target: "es2022",
    sourcemap: false,
  },
  test: {
    // Component and hook tests opt into jsdom with a `// @vitest-environment jsdom` comment.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // Where the junit reporter of `npm run test:ci` writes, for the CI test report.
    outputFile: { junit: "reports/junit-vitest.xml" },
    // Coverage is reported, never enforced (D78): the text total for the badge, Cobertura for merge request diffs.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/**/*.d.ts"],
      reporter: ["text", "cobertura"],
    },
  },
});
