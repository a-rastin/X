import { test } from "node:test";
import { strict as assert } from "node:assert";
import { supportsLegacyWizard } from "../src/features/encounters/workflow.ts";

test("only the server-identified legacy workflow opens the legacy wizard", () => {
  assert.equal(supportsLegacyWizard("legacy-v1"), true);
  for (const value of ["new-case-v1", "future-v1", "", null, undefined]) {
    assert.equal(supportsLegacyWizard(value), false);
  }
});
