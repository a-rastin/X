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

test("registration always validates and submits a birth year", () => {
  const fields = { givenName: "علی", familyName: "رضایی", identifier: "0012345678", sex: "M", birthYear: "1996", clinicalStatus: "established", phone: "+98 21 1234" };
  for (const clinicalStatus of ["established", "first_time"]) {
    const input = { ...fields, clinicalStatus };
    assert.equal(validateDemographics(input, 2026).valid, true);
    assert.deepEqual(toRegistrationPayload(input), { given_name: "علی", family_name: "رضایی", identifier: "0012345678", sex: "M", birth_year: 1996, clinical_status: clinicalStatus, phone: "+98 21 1234" });
    assert.equal(validateDemographics({ ...input, birthYear: "" }, 2026).errors.birthYear, "Select a valid birth year.");
  }
  const invalid = validateDemographics({ ...fields, identifier: "۰۰۱۲۳۴۵۶۷۸", birthYear: "۲۰۰۸" }, 2026);
  assert.equal(present("fa", invalid.errors.identifier), "شناسهٔ بیمار باید دقیقاً ۱۰ رقم ASCII از 0 تا 9 باشد.");
  assert.ok(invalid.errors.birthYear);
  assert.ok(validateDemographics({ ...fields, clinicalStatus: "" }, 2026).errors.clinicalStatus);
});

test("new-case birth-year variant uses strict names and frozen server year boundaries", () => {
  const fields = {givenName:"  Ali  Reza  ", familyName:"رضایی", identifier:"0012345678", sex:"M", birthYear:"1926", clinicalStatus:"first_time", phone:""};
  assert.equal(validateDemographics(fields, 2026).valid, true);
  assert.equal(toRegistrationPayload(fields).birth_year, 1926);
  assert.equal("age" in toRegistrationPayload(fields), false);
  for (const clinicalStatus of ["", "first_time", "established"]) {
    for (const name of ["Anne-Marie", "O'Neil", "Ali1", "Ali\tReza"]) assert.ok(validateDemographics({...fields, clinicalStatus, givenName:name},2026).errors.givenName);
  }
  assert.equal(validateDemographics({...fields,birthYear:"2008"},2026).valid,true);
  for (const birthYear of ["1925","2009","","2008.0","۲۰۰۸"]) assert.equal(validateDemographics({...fields,birthYear},2026).valid,false);
  assert.equal(validateDemographics(fields).valid,false);
});
