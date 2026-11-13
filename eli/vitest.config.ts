import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Generates the .xlsx test files (gitignored) before the run.
    globalSetup: ["test/globalSetup.ts"],
  },
});
