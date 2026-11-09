import { test } from "node:test";
import { strict as assert } from "node:assert";
import { isValidPersonName, validateDemographics, toRegistrationPayload } from "../src/features/patients/validation.ts";
import { present } from "../src/shared/messages.ts";

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

test("Persian presentation leaves validation and ASCII payload contracts unchanged", () => {
  const fields = { givenName: "علی", familyName: "رضایی", identifier: "0012345678", sex: "M", age: "30", clinicalStatus: "first_time", phone: "+98 21 1234" };
  assert.equal(validateDemographics(fields).valid, true);
  assert.deepEqual(toRegistrationPayload(fields), { given_name: "علی", family_name: "رضایی", identifier: "0012345678", sex: "M", age: 30, clinical_status: "first_time", phone: "+98 21 1234" });
  const invalid = validateDemographics({ ...fields, identifier: "۰۰۱۲۳۴۵۶۷۸", age: "۳۰" });
  assert.equal(invalid.valid, false);
  assert.equal(present("fa", invalid.errors.identifier), "شناسهٔ بیمار باید دقیقاً ۱۰ رقم ASCII از 0 تا 9 باشد.");
  assert.equal(present("fa", invalid.errors.age), "سن باید عدد صحیح بین ۱۸ تا ۹۹ باشد.");
  assert.equal(invalid.errors.age, "Age must be an integer 18-99.");
});

test("new-case birth-year variant uses strict names and frozen server year boundaries", () => {
  const fields = {givenName:"  Ali  Reza  ", familyName:"رضایی", identifier:"0012345678", sex:"M", age:"", birthYear:"1926", clinicalStatus:"first_time", phone:""};
  assert.equal(validateDemographics(fields, 2026).valid, true);
  assert.equal(toRegistrationPayload(fields).birth_year, 1926);
  assert.equal("age" in toRegistrationPayload(fields), false);
  for (const name of ["Anne-Marie", "O'Neil", "Ali1", "Ali\tReza"]) assert.equal(validateDemographics({...fields,givenName:name},2026).valid,false);
  assert.equal(validateDemographics({...fields,birthYear:"2008"},2026).valid,true);
  for (const birthYear of ["1925","2009","","2008.0","۲۰۰۸"]) assert.equal(validateDemographics({...fields,birthYear},2026).valid,false);
  assert.equal(validateDemographics(fields).valid,false);
});
