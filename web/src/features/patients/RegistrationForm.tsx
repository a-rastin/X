import { useLanguage } from "../../shared/i18n";
import { Field } from "@astryxdesign/core/Field";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
/* Demographics registration form (S06, plan.md §2.2, FR-10).
 *
 * Physician-only: administrators see a denial (no create button), matching
 * the server 403. The Register button stays disabled until client validation
 * passes; the server stays authoritative — its 422 field_errors render
 * against the matching labels (role=alert, aria-describedby) with keyboard
 * focus moved to the summary, and 409 duplicates keep every keystroke. The
 * identifier is handled as text throughout (leading zeros preserved, never
 * stored numerically). This route is step 1 of the registration wizard.
 * A valid create opens `#/encounters/:id#diagnosis`.
 */

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { newCaseConfig } from "../new-case/api";
import { ApiError } from "../identity/api";
import { useAuth } from "../identity/auth";
import { createPatient } from "./api";
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
  sex: "M",
  birthYear: "",
  clinicalStatus: "first_time",
  phone: "",
};

type Touched = Partial<Record<DemographicsField, boolean>>;

export function RegistrationPage() {
  const { t } = useLanguage();
  const { user, sessionExpired } = useAuth();

  // Route guards complement the server checks: administrators never get the
  // form (the server answers 403), they only read the directory.
  if (user?.role !== "physician") {
    return (
      <div>
        <h2 className="xi-page-title">{t("addPatient")}</h2>
        <p className="xi-form-error" role="alert">
          {t("physicianRequired")}
        </p>
      </div>
    );
  }

  return <RegistrationForm />;
}

export function RegistrationForm() {
  const { t, text, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [registrationYear, setRegistrationYear] = useState<number>();
  useEffect(() => {
    void newCaseConfig()
      .then((c) => setRegistrationYear(c.registration_year))
      .catch(() =>
        setFormError("Registration configuration unavailable. Retry."),
      );
  }, []);
  const [fields, setFields] = useState<DemographicsFields>(EMPTY);
  const [touched, setTouched] = useState<Touched>({});
  const [serverErrors, setServerErrors] = useState<ClientFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const summaryRef = useRef<HTMLParagraphElement>(null);

  const { valid: clientValid, errors: clientErrors } = useMemo(
    () => validateDemographics(fields, registrationYear),
    [fields, registrationYear],
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
      window.location.hash = `#/encounters/${result.draft.id}#diagnosis`;
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

  function describedBy(
    field: DemographicsField,
    error: string | null,
  ): string | undefined {
    return error !== null ? `${field}-error` : undefined;
  }

  const givenNameError = errorFor("givenName");
  const familyNameError = errorFor("familyName");
  const identifierError = errorFor("identifier");
  const sexError = errorFor("sex");
  const clinicalStatusError = errorFor("clinicalStatus");
  const phoneError = errorFor("phone");
  const registrationSteps = [
    { id: "register", label: t("registerPatient") },
    ...["diagnosis", "panss", "harm", "history", "medications", "considerations"].map((id) => ({
      id,
      label: t(("nc." + id) as import("../../shared/messages").MessageKey),
    })),
  ];

  return (
    <div>
      <h2 className="xi-page-title">{t("addPatient")}</h2>
      <nav
        className="xi-wizard-nav"
        aria-label={t("encounterPages")}
        id="wizard-pages"
      >
        {registrationSteps.map((item, index) => {
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
              {index + 1}. {text(item.label)}
            </a>
          );
        })}
      </nav>
      <div
        className="xi-wizard-page"
        id="wizard-page"
        role="region"
        aria-label={t("registerPatientStep1OfValue1", {
          value1: registrationSteps.length,
        })}
      >
        <section className="xi-card" aria-labelledby="demographics-heading">
          <h3 className="xi-section-title" id="demographics-heading">
            {t("stepDemographics")}
          </h3>
          <p className="xi-hint">{t("registrationRules")}</p>
          <form
            className="xi-form-grid"
            aria-label={t("registerPatient")}
            onSubmit={handleSubmit}
            noValidate
          >
            <div className="xi-field">
              <label className="xi-label" htmlFor="reg-given-name">
                {t("givenName")}{" "}
                <span className="nc-required" aria-label={t("nc.required")}>
                  *
                </span>
              </label>
              <input
                dir="auto"
                className="xi-input"
                id="reg-given-name"
                autoComplete="off"
                required
                value={fields.givenName}
                onChange={(event) => setField("givenName", event.target.value)}
                aria-invalid={givenNameError !== null}
                aria-describedby={describedBy("givenName", givenNameError)}
              />
              {givenNameError !== null && (
                <p className="xi-field-error" id="givenName-error">
                  {errorText(givenNameError)}
                </p>
              )}
            </div>
            <div className="xi-field">
              <label className="xi-label" htmlFor="reg-family-name">
                {t("familyName")}{" "}
                <span className="nc-required" aria-label={t("nc.required")}>
                  *
                </span>
              </label>
              <input
                dir="auto"
                className="xi-input"
                id="reg-family-name"
                autoComplete="off"
                required
                value={fields.familyName}
                onChange={(event) => setField("familyName", event.target.value)}
                aria-invalid={familyNameError !== null}
                aria-describedby={describedBy("familyName", familyNameError)}
              />
              {familyNameError !== null && (
                <p className="xi-field-error" id="familyName-error">
                  {errorText(familyNameError)}
                </p>
              )}
            </div>
            <div className="xi-field">
              <label className="xi-label" htmlFor="reg-identifier">
                {t("patientId")}{" "}
                <span className="nc-required" aria-label={t("nc.required")}>
                  *
                </span>
              </label>
              <input
                dir="ltr"
                className="xi-input xi-mono"
                id="reg-identifier"
                inputMode="numeric"
                autoComplete="off"
                maxLength={10}
                required
                value={fields.identifier}
                onChange={(event) => setField("identifier", event.target.value)}
                aria-invalid={identifierError !== null}
                aria-describedby={describedBy("identifier", identifierError)}
              />
              {identifierError !== null && (
                <p className="xi-field-error" id="identifier-error">
                  {errorText(identifierError)}
                </p>
              )}
            </div>
            <Field className="xi-field" label={t("sex")} inputID="reg-sex" labelID="reg-sex-label" isGroupLabel isRequired>
              <SegmentedControl id="reg-sex" aria-labelledby="reg-sex-label" aria-describedby={sexError ? "sex-error" : undefined} label={t("sex")} value={fields.sex} onChange={(value) => setField("sex", value)}>
                <SegmentedControlItem value="M" label="M" />
                <SegmentedControlItem value="F" label="F" />
              </SegmentedControl>
              {sexError !== null && (
                <p className="xi-field-error" id="sex-error">
                  {errorText(sexError)}
                </p>
              )}
            </Field>
            <div className="xi-field">
              <label className="xi-label" htmlFor="reg-birth-year">
                {t("nc.birthYear")}{" "}
                <span className="nc-required" aria-label={t("nc.required")}>
                  *
                </span>
              </label>
              <select
                id="reg-birth-year"
                className="xi-input"
                required
                value={fields.birthYear}
                onChange={(e) => setField("birthYear", e.target.value)}
                aria-invalid={errorFor("birthYear") !== null}
                aria-describedby={errorFor("birthYear") ? "birthYear-error" : undefined}
              >
                <option value="">—</option>
                {registrationYear &&
                  Array.from(
                    { length: 83 },
                    (_, i) => registrationYear - 18 - i,
                  ).map((year) => (
                    <option key={year} value={year}>
                      {year}
                    </option>
                  ))}
              </select>
              {fields.birthYear && registrationYear && (
                <p>
                  {t("age")}:{" "}
                  <bdi>{registrationYear - Number(fields.birthYear)}</bdi>
                </p>
              )}
              {errorFor("birthYear") && (
                <p className="xi-field-error" id="birthYear-error" role="alert">
                  {errorText(errorFor("birthYear")!)}
                </p>
              )}
            </div>
            <Field className="xi-field" label={t("clinicalStatus")} inputID="reg-status" labelID="reg-status-label" isGroupLabel isRequired>
              <SegmentedControl id="reg-status" aria-labelledby="reg-status-label" aria-describedby={clinicalStatusError ? "clinicalStatus-error" : undefined} label={t("clinicalStatus")} value={fields.clinicalStatus} onChange={(value) => setField("clinicalStatus", value)}>
                <SegmentedControlItem value="first_time" label={t("newCase")} />
                <SegmentedControlItem value="established" label={t("knownCase")} />
              </SegmentedControl>
              {clinicalStatusError !== null && (
                <p className="xi-field-error" id="clinicalStatus-error">
                  {errorText(clinicalStatusError)}
                </p>
              )}
            </Field>
            <div className="xi-field">
              <label className="xi-label" htmlFor="reg-phone">
                {t("optionalPhone")}
              </label>
              <input
                dir="ltr"
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
                  {errorText(phoneError)}
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
                {errorText(formError)}
              </p>
            )}
            <div className="xi-row-actions">
              <button
                className="xi-btn xi-btn-primary"
                type="submit"
                disabled={!clientValid || busy}
              >
                {busy ? t("registering") : t("register")}
              </button>
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}

function holdStep(
  event: MouseEvent<HTMLAnchorElement>,
  current: boolean,
): void {
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
