/* Client-side demographics validation (S06, plan.md §2.2).
 *
 * Mirrors the server contract in backend/src/x_insight/cases/patients.py so
 * the Register button stays disabled until the form can succeed — the server
 * remains authoritative and its 422 field_errors are rendered separately.
 * Names validate after NFC normalization (casing preserved); the identifier
 * is matched as text (exactly ten ASCII digits, leading zeros kept); birth
 * year is validated against the server registration year.
 */

import type { ClinicalStatus, RegistrationPayload, Sex } from "./api";

export interface DemographicsFields {
  givenName: string;
  familyName: string;
  identifier: string;
  sex: "" | Sex;
  birthYear: string;
  clinicalStatus: "" | ClinicalStatus;
  phone: string;
}

export type DemographicsField =
  | "givenName"
  | "familyName"
  | "identifier"
  | "sex"
  | "birthYear"
  | "age"
  | "clinicalStatus"
  | "phone";

export type ClientFieldErrors = Partial<Record<DemographicsField, string>>;

const IDENTIFIER_RE = /^[0-9]{10}$/;
export function isValidPersonName(value: string): boolean {
  const normalized = value.normalize("NFC");
  return (
    Array.from(normalized).length <= 100 &&
    /^[\p{L} '\-]+$/u.test(normalized) &&
    /\p{L}/u.test(normalized) &&
    !normalized.startsWith(" ") &&
    !normalized.endsWith(" ") &&
    !normalized.includes("  ")
  );
}

export function isValidIdentifier(value: string): boolean {
  return IDENTIFIER_RE.test(value);
}

export function validateDemographics(
  fields: DemographicsFields,
  registrationYear?: number,
): {
  valid: boolean;
  errors: ClientFieldErrors;
} {
  const errors: ClientFieldErrors = {};
  const validName = (value: string) =>
    Array.from(value.normalize("NFC").replace(/^ +| +$/g, "")).length <= 100 &&
    /^[\p{L} ]+$/u.test(value.normalize("NFC")) &&
    /\p{L}/u.test(value);
  if (!validName(fields.givenName)) {
    errors.givenName = "Name must contain only letters and ordinary spaces.";
  }
  if (!validName(fields.familyName)) {
    errors.familyName = "Name must contain only letters and ordinary spaces.";
  }
  if (!isValidIdentifier(fields.identifier)) {
    errors.identifier = "Patient ID must be exactly 10 ASCII digits (0-9).";
  }
  if (fields.sex !== "M" && fields.sex !== "F") {
    errors.sex = "Select M or F.";
  }
  const year = Number(fields.birthYear);
  if (
    !registrationYear ||
    !/^[0-9]{4}$/.test(fields.birthYear) ||
    registrationYear - year < 18 ||
    registrationYear - year > 100
  ) {
    errors.birthYear = "Select a valid birth year.";
  }
  if (
    fields.clinicalStatus !== "first_time" &&
    fields.clinicalStatus !== "established"
  ) {
    errors.clinicalStatus = "Select new case or known case.";
  }
  // Phone is optional free text with no format validation (plan §2.2).
  return { valid: Object.keys(errors).length === 0, errors };
}

/** Build the POST body from fields already gated by validateDemographics.
 * The identifier is never converted to a number; a blank phone is omitted. */
export function toRegistrationPayload(
  fields: DemographicsFields,
): RegistrationPayload {
  const payload: RegistrationPayload = {
    identifier: fields.identifier,
    given_name: fields.givenName,
    family_name: fields.familyName,
    sex: fields.sex as Sex,
    birth_year: Number(fields.birthYear),
    clinical_status: fields.clinicalStatus as ClinicalStatus,
  };
  if (fields.phone.trim() !== "") {
    payload.phone = fields.phone;
  }
  return payload;
}

/** Map server 422 field names (snake_case) onto client field ids. */
export function toClientField(field: string): DemographicsField | null {
  switch (field) {
    case "given_name":
      return "givenName";
    case "family_name":
      return "familyName";
    case "identifier":
      return "identifier";
    case "sex":
      return "sex";
    case "birth_year":
      return "birthYear";
    case "age":
      return "age";
    case "clinical_status":
      return "clinicalStatus";
    case "phone":
      return "phone";
    default:
      return null;
  }
}
