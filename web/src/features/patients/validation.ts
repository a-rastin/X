/* Client-side demographics validation (S06, plan.md §2.2).
 *
 * Mirrors the server contract in backend/src/x_insight/cases/patients.py so
 * the Register button stays disabled until the form can succeed — the server
 * remains authoritative and its 422 field_errors are rendered separately.
 * Names validate after NFC normalization (casing preserved); the identifier
 * is matched as text (exactly ten ASCII digits, leading zeros kept); age is
 * validated from its raw text so decimals and non-numeric input are caught
 * before anything is stored numerically.
 */

import type { ClinicalStatus, RegistrationPayload, Sex } from "./api";

export interface DemographicsFields {
  givenName: string;
  familyName: string;
  identifier: string;
  sex: "" | Sex;
  age: string;
  clinicalStatus: "" | ClinicalStatus;
  phone: string;
}

export type DemographicsField =
  | "givenName"
  | "familyName"
  | "identifier"
  | "sex"
  | "age"
  | "clinicalStatus"
  | "phone";

export type ClientFieldErrors = Partial<Record<DemographicsField, string>>;

const IDENTIFIER_RE = /^[0-9]{10}$/;
// Integer 18-99 only: rejects decimals ("30.5"), look-alikes ("30.0"), signs,
// whitespace padding, and out-of-range values before any numeric conversion.
const AGE_RE = /^(1[89]|[2-8][0-9]|9[0-9])$/;

export function isValidPersonName(value: string): boolean {
  const normalized = value.normalize("NFC");
  return normalized.length > 0 && /^[\p{L}]+$/u.test(normalized);
}

export function isValidIdentifier(value: string): boolean {
  return IDENTIFIER_RE.test(value);
}

export function isValidAge(value: string): boolean {
  return AGE_RE.test(value.trim());
}

const NAME_MESSAGE =
  "Must be non-empty Unicode letters only (no spaces, digits, or punctuation).";

export function validateDemographics(fields: DemographicsFields): {
  valid: boolean;
  errors: ClientFieldErrors;
} {
  const errors: ClientFieldErrors = {};
  if (!isValidPersonName(fields.givenName)) {
    errors.givenName = `Given name ${NAME_MESSAGE}`;
  }
  if (!isValidPersonName(fields.familyName)) {
    errors.familyName = `Family name ${NAME_MESSAGE}`;
  }
  if (!isValidIdentifier(fields.identifier)) {
    errors.identifier = "Patient ID must be exactly 10 ASCII digits (0-9).";
  }
  if (fields.sex !== "M" && fields.sex !== "F") {
    errors.sex = "Select M or F.";
  }
  if (!isValidAge(fields.age)) {
    errors.age = "Age must be an integer 18-99.";
  }
  if (fields.clinicalStatus !== "first_time" && fields.clinicalStatus !== "established") {
    errors.clinicalStatus = "Select first-time or established.";
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
    age: Number.parseInt(fields.age.trim(), 10),
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
