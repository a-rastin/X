import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // The real bundled release requires a fresh DB, separate from synthetic suites.
  testIgnore: "**/ddi-fresh-install.spec.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
  ],
});
