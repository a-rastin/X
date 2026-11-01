import { test } from "node:test";
import { strict as assert } from "node:assert";
import { parseHash, routeToHash, chartHash, patientIdFromHash } from "../src/app/router.ts";

test("static routes round trip", () => {
  for (const route of ["login", "dashboard", "provider-settings", "status"]) {
    assert.equal(parseHash(routeToHash(route)), route);
  }
});

test("patient chart hashes retain the patient identifier", () => {
  const hash = chartHash("fictional-patient");
  assert.equal(parseHash(hash), "chart");
  assert.equal(patientIdFromHash(hash), "fictional-patient");
  assert.equal(patientIdFromHash("#/patients//chart"), null);
});
