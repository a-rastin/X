import { defineConfig } from "@playwright/test";
import base from "../playwright.config";

export default defineConfig({
  ...base,
  testDir: ".",
  testIgnore: [],
  testMatch: "ddi-fresh-install.spec.ts",
});
