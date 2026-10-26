import { defineConfig } from "@playwright/test";
import base from "../playwright.config";

// Each browser needs its own empty database/application pair for first-save proof.
export default defineConfig({
  ...base,
  testDir: ".",
  testIgnore: [],
  testMatch: "provider-public-onboarding.spec.ts",
  workers: 1,
});
