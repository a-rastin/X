/* Demographics registration form (S06, plan.md §2.2, FR-10).
 *
 * Physician-only: administrators see a denial (no create button), matching
 * the server 403. The Register button stays disabled until client validation
 * passes; the server stays authoritative — its 422 field_errors render
 * against the matching labels (role=alert, aria-describedby) with keyboard
 * focus moved to the summary, and 409 duplicates keep every keystroke. The
 * identifier is handled as text throughout (leading zeros preserved, never
 * stored numerically). This route is step 1 of the registration wizard.
 * Notes are visible here but stay local until a valid create; that create
 * opens `#/encounters/:id#register`, where the same step saves them.
 */

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { ApiError } from "../identity/api";
import { useAuth } from "../identity/auth";
import { createPatient } from "./api";
import { REGISTRATION_WIZARD_STEPS } from "../encounters/EncounterPage";
import { stageRegistrationNotice } from "../encounters/DemographicsDraftPanels";
import {
  toClientField,
  toRegistrationPayload,
  validateDemographics,
  type ClientFieldErrors,
  type DemographicsField,
  type DemographicsFields,
} from "./validation";

const EMPTY: DemographicsFields = {
  givenName: "",
  familyName: "",
  identifier: "",
  sex: "",
  age: "",
  clinicalStatus: "",
  phone: "",
};

type Touched = Partial<Record<DemographicsField, boolean>>;

export function RegistrationPage() {
  const { user, sessionExpired } = useAuth();

  // Route guards complement the server checks: administrators never get the
  // form (the server answers 403), they only read the directory.
  if (user?.role !== "physician") {
    return (
      <div>
        <h2 className="xi-page-title">Register patient</h2>
        <p className="xi-form-error" role="alert">
          Physician access required. Administrators read the patient directory;
          they do not register patients.
        </p>
      </div>
    );
  }

  return <RegistrationForm />;
}

function RegistrationForm() {
  const { sessionExpired } = useAuth();
  const [fields, setFields] = useState<DemographicsFields>(EMPTY);
  const [touched, setTouched] = useState<Touched>({});
  const [serverErrors, setServerErrors] = useState<ClientFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const summaryRef = useRef<HTMLParagraphElement>(null);

  const { valid: clientValid, errors: clientErrors } = useMemo(
    () => validateDemographics(fields),
    [fields],
  );

  // Keyboard focus follows the error summary once it commits: server
  // verdicts (409/422/403/transport) announce via role=alert and land focus
  // for keyboard and screen-reader users alike.
  useEffect(() => {
    if (formError !== null) {
      summaryRef.current?.focus();
    }
  }, [formError]);

  function setField(field: DemographicsField, value: string): void {
    setFields((previous) => ({ ...previous, [field]: value }));
    setTouched((previous) => ({ ...previous, [field]: true }));
    // A new keystroke supersedes the last server verdict for that field;
    // untouched server errors stay until the next submit re-checks them.
    setServerErrors((previous) => {
      if (previous[field] === undefined) {
        return previous;
      }
      const next = { ...previous };
      delete next[field];
      return next;
    });
  }

  /** Client hint for touched fields, else the standing server error. */
  function errorFor(field: DemographicsField): string | null {
    if (touched[field] === true && clientErrors[field] !== undefined) {
      return clientErrors[field] ?? null;
    }
    return serverErrors[field] ?? null;
  }

  async function handleSubmit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!clientValid || busy) {
      return;
    }
    setServerErrors({});
    setFormError(null);
    setBusy(true);
    let created = false;
    try {
      const result = await createPatient(toRegistrationPayload(fields));
      stageRegistrationNotice({
        draftId: result.draft.id,
        identifier: result.patient.identifier,
        serverTimestamp: result.server_timestamp,
        revision: result.revision,
      });
      created = true;
      // Same assignment the wizard already uses, so the step fragment is not
      // encoded away. Back returns to a fresh form, not a second submit.
      window.location.hash = `#/encounters/${result.draft.id}#register`;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        // Duplicate identifier (including archived rows): keep every
        // keystroke, point at the identifier, announce once.
        setServerErrors({ identifier: "Already exists." });
        setFormError(err.message);
        return;
      }
      if (err instanceof ApiError && err.status === 422) {
        const mapped: ClientFieldErrors = {};
        const unmapped: string[] = [];
        for (const [serverField, messages] of Object.entries(err.fieldErrors)) {
          const clientField = toClientField(serverField);
          if (clientField === null) {
            unmapped.push(`${serverField}: ${messages.join(" ")}`);
          } else {
            mapped[clientField] = messages.join(" ");
          }
        }
        setServerErrors(mapped);
        setFormError(
          unmapped.length > 0
            ? `${err.message} ${unmapped.join(" ")}`
            : err.message,
        );
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setFormError(err.message);
        return;
      }
      setFormError("Registration failed. Check the connection and retry.");
    } finally {
      if (!created) {
        setBusy(false);
      }
    }
  }

  function describedBy(field: DemographicsField, error: string | null): string | undefined {
    return error !== null ? `${field}-error` : undefined;
  }

  const givenNameError = errorFor("givenName");
  const familyNameError = errorFor("familyName");
  const identifierError = errorFor("identifier");
  const sexError = errorFor("sex");
  const ageError = errorFor("age");
  const clinicalStatusError = errorFor("clinicalStatus");
  const phoneError = errorFor("phone");

  return (
    <div>
      <h2 className="xi-page-title">Register patient</h2>
      <nav className="xi-wizard-nav" aria-label="Encounter pages" id="wizard-pages">
        {REGISTRATION_WIZARD_STEPS.map((item, index) => {
          const current = item.id === "register";
          return (
            <a
              key={item.id}
              id={`wizard-step-${item.id}`}
              href="#/patients/new"
              aria-current={current ? "page" : undefined}
              aria-disabled={current ? undefined : true}
              tabIndex={current ? undefined : -1}
              onClick={(event) => holdStep(event, current)}
            >
              {index + 1}. {item.label}
            </a>
          );
        })}
      </nav>
      <div
        className="xi-wizard-page"
        id="wizard-page"
        role="region"
        aria-label={`Register patient, step 1 of ${REGISTRATION_WIZARD_STEPS.length}`}
      >
      <section className="xi-card" aria-labelledby="demographics-heading">
        <h3 className="xi-section-title" id="demographics-heading">
          Demographics (step 1)
        </h3>
        <p className="xi-hint">
          Names use Unicode letters only; the patient ID is exactly 10 ASCII
          digits and keeps leading zeros. Nothing is sent until the form is
          valid. Later steps open after the patient is registered.
        </p>
        <form
          className="xi-form-grid"
          aria-label="Register patient"
          onSubmit={handleSubmit}
          noValidate
        >
          <div className="xi-field">
            <label className="xi-label" htmlFor="reg-given-name">
              Given name
            </label>
            <input
              className="xi-input"
              id="reg-given-name"
              autoComplete="off"
              value={fields.givenName}
              onChange={(event) => setField("givenName", event.target.value)}
              aria-invalid={givenNameError !== null}
              aria-describedby={describedBy("givenName", givenNameError)}
            />
            {givenNameError !== null && (
              <p className="xi-field-error" id="givenName-error">
                {givenNameError}
              </p>
            )}
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor="reg-family-name">
              Family name
            </label>
            <input
              className="xi-input"
              id="reg-family-name"
              autoComplete="off"
              value={fields.familyName}
              onChange={(event) => setField("familyName", event.target.value)}
              aria-invalid={familyNameError !== null}
              aria-describedby={describedBy("familyName", familyNameError)}
            />
            {familyNameError !== null && (
              <p className="xi-field-error" id="familyName-error">
                {familyNameError}
              </p>
            )}
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor="reg-identifier">
              Patient ID
            </label>
            <input
              className="xi-input xi-mono"
              id="reg-identifier"
              inputMode="numeric"
              autoComplete="off"
              maxLength={10}
              value={fields.identifier}
              onChange={(event) => setField("identifier", event.target.value)}
              aria-invalid={identifierError !== null}
              aria-describedby={describedBy("identifier", identifierError)}
            />
            {identifierError !== null && (
              <p className="xi-field-error" id="identifier-error">
                {identifierError}
              </p>
            )}
          </div>
          <fieldset
            className="xi-field"
            aria-describedby={sexError ? "sex-error" : undefined}
          >
            <legend className="xi-label">Sex</legend>
            <div className="xi-radio-group" role="radiogroup" aria-label="Sex">
              <label htmlFor="reg-sex-m">
                <input
                  id="reg-sex-m"
                  type="radio"
                  name="sex"
                  value="M"
                  checked={fields.sex === "M"}
                  onChange={() => setField("sex", "M")}
                />
                M
              </label>
              <label htmlFor="reg-sex-f">
                <input
                  id="reg-sex-f"
                  type="radio"
                  name="sex"
                  value="F"
                  checked={fields.sex === "F"}
                  onChange={() => setField("sex", "F")}
                />
                F
              </label>
            </div>
            {sexError !== null && (
              <p className="xi-field-error" id="sex-error">
                {sexError}
              </p>
            )}
          </fieldset>
          <div className="xi-field">
            <label className="xi-label" htmlFor="reg-age">
              Age
            </label>
            <input
              className="xi-input"
              id="reg-age"
              inputMode="numeric"
              autoComplete="off"
              value={fields.age}
              onChange={(event) => setField("age", event.target.value)}
              aria-invalid={ageError !== null}
              aria-describedby={describedBy("age", ageError)}
            />
            {ageError !== null && (
              <p className="xi-field-error" id="age-error">
                {ageError}
              </p>
            )}
          </div>
          <fieldset
            className="xi-field"
            aria-describedby={
              clinicalStatusError ? "clinicalStatus-error" : undefined
            }
          >
            <legend className="xi-label">Clinical status</legend>
            <div
              className="xi-radio-group"
              role="radiogroup"
              aria-label="Clinical status"
            >
              <label htmlFor="reg-status-first">
                <input
                  id="reg-status-first"
                  type="radio"
                  name="clinical_status"
                  value="first_time"
                  checked={fields.clinicalStatus === "first_time"}
                  onChange={() => setField("clinicalStatus", "first_time")}
                />
                First-time
              </label>
              <label htmlFor="reg-status-established">
                <input
                  id="reg-status-established"
                  type="radio"
                  name="clinical_status"
                  value="established"
                  checked={fields.clinicalStatus === "established"}
                  onChange={() => setField("clinicalStatus", "established")}
                />
                Established
              </label>
            </div>
            {clinicalStatusError !== null && (
              <p className="xi-field-error" id="clinicalStatus-error">
                {clinicalStatusError}
              </p>
            )}
          </fieldset>
          <div className="xi-field">
            <label className="xi-label" htmlFor="reg-phone">
              Phone (optional)
            </label>
            <input
              className="xi-input"
              id="reg-phone"
              type="tel"
              autoComplete="off"
              value={fields.phone}
              onChange={(event) => setField("phone", event.target.value)}
              aria-invalid={phoneError !== null}
              aria-describedby={describedBy("phone", phoneError)}
            />
            {phoneError !== null && (
              <p className="xi-field-error" id="phone-error">
                {phoneError}
              </p>
            )}
          </div>
          {formError !== null && (
            <p
              className="xi-form-error"
              role="alert"
              id="registration-error"
              tabIndex={-1}
              ref={summaryRef}
            >
              {formError}
            </p>
          )}
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="submit"
              disabled={!clientValid || busy}
            >
              {busy ? "Registering…" : "Register patient"}
            </button>
          </div>
        </form>
      </section>
      <PendingRegistrationNotes />
      </div>
    </div>
  );
}

function holdStep(event: MouseEvent<HTMLAnchorElement>, current: boolean): void {
  if (
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    event.button !== 0
  ) {
    return;
  }
  event.preventDefault();
  if (!current) {
    return;
  }
}

/** Same two boxes as step 1 after create. Not durable until a draft exists. */
function PendingRegistrationNotes() {
  return (
    <>
      <section className="xi-card" aria-labelledby="notes-demographics-heading">
        <h4 className="xi-section-title" id="notes-demographics-heading">
          Page notes — demographics
        </h4>
        <p className="xi-hint">
          Attributed notes for this page. They can be left empty. They save
          after the patient is registered — nothing here is saved yet.
        </p>
        <div className="xi-field">
          <label className="xi-label" htmlFor="notes-demographics-input">
            Page note for demographics
          </label>
          <textarea
            className="xi-input"
            id="notes-demographics-input"
            rows={3}
            disabled
            autoComplete="off"
          />
        </div>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          id="notes-demographics-add"
          disabled
        >
          Add page note
        </button>
      </section>
      <section className="xi-card" aria-labelledby="draft-editor-heading">
        <h3 className="xi-section-title" id="draft-editor-heading">
          Working note (draft only)
        </h3>
        <p className="xi-hint">
          Autosaves after the patient is registered. Leave it empty, or fill
          it on this step once the draft exists.
        </p>
        <div className="xi-field">
          <label className="xi-label" htmlFor="draft-working-note">
            Draft working note
          </label>
          <textarea
            className="xi-input"
            id="draft-working-note"
            rows={4}
            disabled
            autoComplete="off"
          />
        </div>
      </section>
    </>
  );
}
