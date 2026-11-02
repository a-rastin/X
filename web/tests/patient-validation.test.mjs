import { test } from "node:test";
import { strict as assert } from "node:assert";
import { isValidPersonName, validateDemographics } from "../src/features/patients/validation.ts";

test("patient names accept the specified Unicode names and code-point boundary", () => {
  for (const value of ["Patient Alpha", "Anne-Marie", "O'Neil", "Élodie", "E\u0301lodie", "𐐀".repeat(100), "-A''"]) {
    assert.equal(isValidPersonName(value), true, JSON.stringify(value));
  }
  for (const value of ["", "---'", "A1", "A@", " A", "A ", "A  B", "A\tB", "A\nB", "A\u00a0B", "A\u0301\u0301", "𐐀".repeat(101)]) {
    assert.equal(isValidPersonName(value), false, JSON.stringify(value));
  }
});

test("invalid names give naturally worded field-specific messages", () => {
  const { errors } = validateDemographics({ givenName: "Test1", familyName: "Alpha@", identifier: "0123456789", sex: "M", age: "30", clinicalStatus: "first_time", phone: "" });
  assert.equal(errors.givenName, "Given name may contain letters, spaces, hyphens and apostrophes. Use 1–100 characters, with no leading, trailing or double spaces.");
  assert.equal(errors.familyName, "Family name may contain letters, spaces, hyphens and apostrophes. Use 1–100 characters, with no leading, trailing or double spaces.");
});
