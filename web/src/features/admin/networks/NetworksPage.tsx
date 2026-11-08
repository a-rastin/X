import { useLanguage } from "../../../shared/i18n";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  activateBundle,
  createVersion,
  exportXml,
  importNetwork,
  listNetworks,
  listVersions,
  rollbackBundle,
  validateVersion,
  type BundleResult,
  type NetworkItem,
  type ReviewDecision,
  type ReviewInput,
  type ValidationReport,
  type VersionItem,
  type Workflow,
} from "./api";
import { GraphView } from "./GraphView";
import { useAuth } from "../../identity/auth";

/* Model version administration (S24 slices 1–2, seam T9).
 *
 * Admin can import XML, list networks/versions, see version history plus
 * separate structural/semantic/content/admission validation details, export
 * exact XML, and move the workflow pointer with explicit activation/rollback
 * confirmation. The read-only graph below the validation report renders
 * ordered nodes/edges/states from the real graph endpoint. Physician access
 * is denied by the wrapper in app/pages.tsx (route guard complements the
 * server 403, never replaces it).
 *
 * Wording rule: XSD/structural success is never labeled executable or
 * clinically valid; `admission.executable` is pipeline admission only.
 *
 * Stable wiring for the future dev-test e2e/networks.spec.ts (route
 * `#/networks`):
 * - heading `data-testid="networks-heading"` (`#networks-heading`)
 * - import form `data-testid="networks-import-form"` (`#networks-import-form`)
 * - network list `data-testid="networks-list"` (`#networks-list`)
 * - version history `data-testid="networks-versions"` (`#networks-versions`)
 * - validation report `data-testid="networks-validation"` (`#networks-validation`)
 * - graph `data-testid="networks-graph"` (`#networks-graph`)
 * - activation panel `data-testid="networks-activation"` (`#networks-activation`)
 * - per-version export `data-testid="networks-export-xml"` (one per version row)
 */

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function ReviewFields({
  idPrefix,
  review,
  onChange,
}: {
  idPrefix: string;
  review: ReviewInput;
  onChange: (next: ReviewInput) => void;
}) {
  const { t } = useLanguage();
  return (
    <>
      <div className="xi-field">
        <label className="xi-label" htmlFor={`${idPrefix}-reviewer`}>
          
          {t("reviewer")}
        </label>
        <input
          className="xi-input"
          id={`${idPrefix}-reviewer`}
          value={review.reviewer}
          onChange={(event) => onChange({ ...review, reviewer: event.target.value })}
        />
      </div>
      <div className="xi-field">
        <label className="xi-label" htmlFor={`${idPrefix}-decision`}>
          
          {t("reviewDecision")}
        </label>
        <select
          className="xi-select"
          id={`${idPrefix}-decision`}
          value={review.decision}
          onChange={(event) =>
            onChange({ ...review, decision: event.target.value as ReviewDecision })
          }
        >
          <option value="draft">{t("draft291")}</option>
          <option value="awaiting_review">{t("awaitingReview")}</option>
          <option value="approved">{t("approved")}</option>
        </select>
      </div>
      <div className="xi-field">
        <label className="xi-label" htmlFor={`${idPrefix}-date`}>
          
          {t("reviewDate")}
        </label>
        <input
          className="xi-input"
          id={`${idPrefix}-date`}
          type="date"
          value={review.date}
          onChange={(event) => onChange({ ...review, date: event.target.value })}
        />
      </div>
    </>
  );
}

function ImportForm({ onImported }: { onImported: () => void }) {
  const { t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [name, setName] = useState("");
  const [xmlText, setXmlText] = useState("");
  const [review, setReview] = useState<ReviewInput>({
    reviewer: "",
    decision: "approved",
    date: todayDate(),
  });
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    setDone(null);
    setBusy(true);
    try {
      const result = await importNetwork(name.trim(), xmlText, review);
      setName("");
      setXmlText("");
      setDone(
        `Imported ${result.network.name} as version ${result.version.version_number}.`,
      );
      onImported();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
        setFormError(err.message);
        return;
      }
      setFormError("Import failed. Check the connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="xi-card" aria-labelledby="import-heading">
      <h3 className="xi-section-title" id="import-heading">
        
        {t("importNetworkXml")}
      </h3>
      <form
        aria-label={t("importNetwork")}
        data-testid="networks-import-form"
        id="networks-import-form"
        onSubmit={handleSubmit}
        noValidate
      >
        <div className="xi-field">
          <label className="xi-label" htmlFor="import-name">
            
            {t("networkName")}
          </label>
          <input
            className="xi-input"
            id="import-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-invalid={fieldErrors["name"] !== undefined}
            aria-describedby={fieldErrors["name"] ? "import-name-error" : undefined}
          />
          {fieldErrors["name"] && (
            <p className="xi-field-error" id="import-name-error">
              {fieldErrors["name"].join(" ")}
            </p>
          )}
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="import-xml">
            
            {t("xmlbifXml")}
          </label>
          <textarea
            className="xi-input"
            id="import-xml"
            rows={8}
            spellCheck={false}
            value={xmlText}
            onChange={(event) => setXmlText(event.target.value)}
            aria-invalid={fieldErrors["xml_text"] !== undefined}
            aria-describedby={fieldErrors["xml_text"] ? "import-xml-error" : undefined}
          />
          {fieldErrors["xml_text"] && (
            <p className="xi-field-error" id="import-xml-error">
              {fieldErrors["xml_text"].join(" ")}
            </p>
          )}
        </div>
        <ReviewFields idPrefix="import" review={review} onChange={setReview} />
        {formError !== null && (
          <p className="xi-form-error" role="alert">
            {errorText(formError)}
          </p>
        )}
        {done !== null && (
          <p className="xi-status" role="status">
            {done}
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? t("importing") : t("importNetwork")}
        </button>
      </form>
    </section>
  );
}

function ValidationDetails({ versionId }: { versionId: string }) {
  const { text: uiText, t } = useLanguage();
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error"; message: string }
    | { kind: "ready"; report: ValidationReport }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    validateVersion(versionId)
      .then((report) => {
        if (!cancelled) {
          setState({ kind: "ready", report });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          sessionExpired();
          return;
        }
        setState({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "Validation failed to load. Check the connection and retry.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, sessionExpired]);

  if (state.kind === "loading") {
    return (
      <section aria-labelledby="validation-heading" aria-busy="true">
        <h4 id="validation-heading">{t("validationDetails")}</h4>
        <p>{t("loadingValidation")}</p>
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section aria-labelledby="validation-heading">
        <h4 id="validation-heading">{t("validationDetails")}</h4>
        <p className="xi-form-error" role="alert">
          {uiText(state.message)}
        </p>
      </section>
    );
  }
  const report = state.report;
  return (
    <section
      aria-labelledby="validation-heading"
      data-testid="networks-validation"
      id="networks-validation"
    >
      <h4 id="validation-heading">{t("validationDetails")}</h4>
      <p className="xi-hint">
        {t("status")}{uiText(report.status)}  {t("review304")} {uiText(report.review.decision)}
        {report.review.reviewer ? t("byValue1", { value1: report.review.reviewer }) : ""}  {t("source305")}{" "}
        {report.source_hash.slice(0, 12)}…
      </p>
      <h5>{t("structuralXsd")}</h5>
      <p>
        <span className={report.structural.xsd_valid ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {report.structural.xsd_valid ? t("xsdStructurallyValid") : t("xsdStructurallyInvalid")}
        </span>{" "}
        <span className="xi-badge">
          {report.structural.activatable_v1
            ? t("activatableV1Admission")
            : t("notActivatableV1Admission")}
        </span>
      </p>
      {report.structural.nonactivatable_reasons.length > 0 && (
        <ul aria-label={t("structuralReasonsThisVersionCannotActivate")}>
          {report.structural.nonactivatable_reasons.map((reason) => (
            <li key={reason}>{uiText(reason)}</li>
          ))}
        </ul>
      )}
      {report.structural.xsd_errors.length > 0 && (
        <ul aria-label={t("xsdErrors")}>
          {report.structural.xsd_errors.map((error, index) => (
            <li key={index}>
              
              {t("line")} {error.line}{t("column")} {error.column}: {uiText(error.message)}
            </li>
          ))}
        </ul>
      )}
      <h5>{t("semantic")}</h5>
      <p>
        <span className={report.semantic.valid ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {report.semantic.valid ? t("semanticsValid") : t("semanticsInvalid")}
        </span>
      </p>
      {report.semantic.errors.length > 0 && (
        <ul aria-label={t("semanticErrors")}>
          {report.semantic.errors.map((error, index) => (
            <li key={`${error.code}-${index}`}>
              {error.code}
              {error.node ? ` (${error.node})` : ""}: {uiText(error.message)}
            </li>
          ))}
        </ul>
      )}
      <h5>{t("contentQuestionPackage")}</h5>
      <p>
        <span className={report.content.valid ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {report.content.valid ? t("contentValid") : t("contentInvalid")}
        </span>
      </p>
      {report.content.errors.length > 0 && (
        <ul aria-label={t("contentErrors")}>
          {report.content.errors.map((error, index) => (
            <li key={`${error.code}-${index}`}>
              {error.code}: {uiText(error.message)}
            </li>
          ))}
        </ul>
      )}
      <h5>{t("pipelineAdmission")}</h5>
      <p>
        <span className={report.admission.executable ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {report.admission.executable ? t("executable") : t("notExecutable")}
        </span>{" "}
        <span className="xi-hint">{t("pipelineAdmissionOnlyNeverClinicalValidity")}</span>
      </p>
      {report.admission.diagnostics.length > 0 && (
        <ul aria-label={t("admissionDiagnostics")}>
          {report.admission.diagnostics.map((diagnostic, index) => (
            <li key={`${diagnostic.code}-${index}`}>
              {diagnostic.code}: {uiText(diagnostic.message)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BundlePanel({
  network,
  version,
}: {
  network: NetworkItem;
  version: VersionItem;
}) {
  const { t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [workflow, setWorkflow] = useState<Workflow>("registration");
  const [review, setReview] = useState<ReviewInput>({
    reviewer: "",
    decision: "approved",
    date: todayDate(),
  });
  const [expectedRevision, setExpectedRevision] = useState("");
  const [confirming, setConfirming] = useState<"activate" | "rollback" | null>(null);
  const [result, setResult] = useState<BundleResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleConfirm() {
    if (confirming === null) {
      return;
    }
    const action = confirming;
    setConfirming(null);
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const trimmed = expectedRevision.trim();
      const revision = trimmed === "" ? null : Number(trimmed);
      if (revision !== null && (!Number.isInteger(revision) || revision < 0)) {
        setError("Expected revision must be a non-negative integer or blank.");
        return;
      }
      const input = {
        workflow,
        networkId: network.id,
        versionId: version.id,
        review,
        expectedRevision: revision,
      };
      const outcome = action === "activate" ? await activateBundle(input) : await rollbackBundle(input);
      setResult(outcome);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        setError(`${err.message} Another administrator moved the pointer first.`);
        return;
      }
      setError(
        err instanceof ApiError
          ? err.message
          : "Pointer move failed. Check the connection and retry.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="activation-heading"
      data-testid="networks-activation"
      id="networks-activation"
    >
      <h4 id="activation-heading">{t("workflowActivation")}</h4>
      <p className="xi-hint">
        
        {t("target")} {network.name}  {t("version")} {version.version_number} ({version.source_hash.slice(0, 12)}
        
        {t("activationRejectsIncompleteOrUnreviewedBundlesThe")}
      </p>
      <div className="xi-field">
        <label className="xi-label" htmlFor="bundle-workflow">
          
          {t("workflow")}
        </label>
        <select
          className="xi-select"
          id="bundle-workflow"
          value={workflow}
          onChange={(event) => setWorkflow(event.target.value as Workflow)}
        >
          <option value="registration">{t("registration")}</option>
          <option value="followup">{t("followup")}</option>
        </select>
      </div>
      <ReviewFields idPrefix="bundle" review={review} onChange={setReview} />
      <div className="xi-field">
        <label className="xi-label" htmlFor="bundle-revision">
          
          {t("expectedPointerRevisionIfMatchBlankMeans")}
        </label>
        <input
          className="xi-input"
          id="bundle-revision"
          inputMode="numeric"
          value={expectedRevision}
          onChange={(event) => setExpectedRevision(event.target.value)}
        />
      </div>
      {confirming === null ? (
        <div className="xi-row-actions">
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            disabled={busy}
            onClick={() => {
              setError(null);
              setResult(null);
              setConfirming("activate");
            }}
          >
            
            {t("activate")}
          </button>
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            disabled={busy}
            onClick={() => {
              setError(null);
              setResult(null);
              setConfirming("rollback");
            }}
          >
            
            {t("rollBack")}
          </button>
        </div>
      ) : (
        <div className="xi-warning-panel" role="alertdialog" aria-labelledby="bundle-confirm-heading" aria-describedby="bundle-confirm-detail">
          <h5 id="bundle-confirm-heading" style={{ marginTop: 0 }}>
            
            {t("confirm")} {confirming === "activate" ? t("activation") : t("rollback")}
          </h5>
          <p id="bundle-confirm-detail" style={{ marginBottom: 8 }}>
            {confirming === "activate" ? t("activate341") : t("rollBack342")}  {t("workflow343")}{" "}
            <strong>{workflow}</strong>  {t("to")} {network.name}  {t("version")}{" "}
            {version.version_number}  {t("withReview")} {review.decision}
            {review.reviewer ? t("byValue1", { value1: review.reviewer }) : ""}{t("expectedRevision")}{" "}
            {expectedRevision.trim() === ""
              ? t("noneMovesUnconditionally")
              : t("ifMatchValue1", { value1: expectedRevision.trim() })}.
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              disabled={busy}
              onClick={() => void handleConfirm()}
            >
              {busy ? t("working") : t("confirmValue1", { value1: confirming === "activate" ? t("activation") : t("rollback") })}
            </button>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => setConfirming(null)}
            >
              {t("cancel")}</button>
          </div>
        </div>
      )}
      {error !== null && (
        <p className="xi-form-error" role="alert">
          {errorText(error)}
        </p>
      )}
      {result !== null && (
        <p className="xi-status" role="status">
          
          {t("pointerRevision")} {result.revision}
          {result.etag ? t("etagValue1", { value1: result.etag }) : t("noEtagReturned")}  {t("forWorkflow")}{" "}
          {result.workflow}.
        </p>
      )}
    </section>
  );
}

function VersionHistory({ network }: { network: NetworkItem }) {
  const { text: uiText, t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error"; message: string }
    | { kind: "ready"; items: VersionItem[]; total: number }
  >({ kind: "loading" });
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [newXml, setNewXml] = useState("");
  const [newReview, setNewReview] = useState<ReviewInput>({
    reviewer: "",
    decision: "approved",
    date: todayDate(),
  });
  const [editError, setEditError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setState((previous) =>
      previous.kind === "ready"
        ? { kind: "ready", items: previous.items, total: previous.total }
        : { kind: "loading" },
    );
    try {
      const result = await listVersions(network.id);
      setState({ kind: "ready", items: result.items, total: result.total });
      setSelectedVersionId((previous) =>
        previous !== null && result.items.some((item) => item.id === previous)
          ? previous
          : (result.items[0]?.id ?? null),
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Version history failed to load. Check the connection and retry.",
      });
    }
  }, [network.id, sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handleNewVersion(event: React.FormEvent) {
    event.preventDefault();
    setEditError(null);
    setBusy(true);
    try {
      await createVersion(network.id, newXml, newReview);
      setNewXml("");
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setEditError(
        err instanceof ApiError
          ? err.message
          : "New version failed. Check the connection and retry.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleExport(version: VersionItem) {
    setExportError(null);
    setExportingId(version.id);
    try {
      // Exact bytes: the text is downloaded unchanged as a Blob, never
      // re-serialized, so the file matches the stored import byte for byte.
      const text = await exportXml(version.id);
      const blob = new Blob([text], { type: "application/xml" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${network.name}-v${version.version_number}.xml`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setExportError(
        err instanceof ApiError
          ? err.message
          : "Export failed. Check the connection and retry.",
      );
    } finally {
      setExportingId(null);
    }
  }

  const selected = state.kind === "ready" ? state.items.find((item) => item.id === selectedVersionId) ?? null : null;

  return (
    <div data-testid="networks-versions" id="networks-versions">
      <h3 className="xi-section-title">{t("versionHistory")} {network.name}</h3>
      {state.kind === "loading" && <p>{t("loadingVersions")}</p>}
      {state.kind === "error" && (
        <div>
          <p className="xi-form-error" role="alert">
            {uiText(state.message)}
          </p>
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void reload()}
          >
            {t("retry")}</button>
        </div>
      )}
      {state.kind === "ready" &&
        (state.items.length === 0 ? (
          <p>{t("noVersionsYet")}</p>
        ) : (
          <table className="xi-table" aria-label={t("versionsOfValue1", { value1: network.name })}>
            <thead>
              <tr>
                <th scope="col">{t("version354")}</th>
                <th scope="col">{t("status")}</th>
                <th scope="col">{t("review")}</th>
                <th scope="col">{t("sourceHash")}</th>
                <th scope="col">{t("actions")}</th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((version) => (
                <tr key={version.id}>
                  <td>v{version.version_number}</td>
                  <td>{uiText(version.status)}</td>
                  <td>{version.review.decision}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                    {version.source_hash.slice(0, 12)}…
                  </td>
                  <td>
                    <div className="xi-row-actions">
                      <button
                        className="xi-btn xi-btn-secondary"
                        type="button"
                        onClick={() => setSelectedVersionId(version.id)}
                      >
                        
                        {t("inspect")}
                      </button>
                      <button
                        className="xi-btn xi-btn-secondary"
                        type="button"
                        data-testid="networks-export-xml"
                        disabled={exportingId === version.id}
                        onClick={() => void handleExport(version)}
                      >
                        {exportingId === version.id ? t("exporting") : t("exportXml")}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      {exportError !== null && (
        <p className="xi-form-error" role="alert">
          {errorText(exportError)}
        </p>
      )}
      <section className="xi-card" aria-labelledby="new-version-heading">
        <h4 id="new-version-heading">{t("newImmutableVersionEditAsNewRow")}</h4>
        <p className="xi-hint">
          
          {t("editingNeverRewritesHistoryTheNewXml")}
        </p>
        <form aria-label={t("createVersion")} onSubmit={handleNewVersion} noValidate>
          <div className="xi-field">
            <label className="xi-label" htmlFor={`version-xml-${network.id}`}>
              
              {t("xmlbifXml")}
            </label>
            <textarea
              className="xi-input"
              id={`version-xml-${network.id}`}
              rows={6}
              spellCheck={false}
              value={newXml}
              onChange={(event) => setNewXml(event.target.value)}
            />
          </div>
          <ReviewFields
            idPrefix={`version-${network.id}`}
            review={newReview}
            onChange={setNewReview}
          />
          {editError !== null && (
            <p className="xi-form-error" role="alert">
              {errorText(editError)}
            </p>
          )}
          <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
            {busy ? t("saving") : t("saveAsNewVersion")}
          </button>
        </form>
      </section>
      {selected !== null && (
        <>
          <ValidationDetails key={`validation-${selected.id}`} versionId={selected.id} />
          <GraphView key={`graph-${selected.id}`} versionId={selected.id} />
          <div className="xi-card">
            <BundlePanel network={network} version={selected} />
          </div>
        </>
      )}
    </div>
  );
}

export function NetworksPage() {
  const { text: uiText, t } = useLanguage();
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error"; message: string }
    | { kind: "ready"; items: NetworkItem[]; total: number }
  >({ kind: "loading" });
  const [selectedNetworkId, setSelectedNetworkId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setState((previous) =>
      previous.kind === "ready"
        ? { kind: "ready", items: previous.items, total: previous.total }
        : { kind: "loading" },
    );
    try {
      const result = await listNetworks();
      setState({ kind: "ready", items: result.items, total: result.total });
      setSelectedNetworkId((previous) =>
        previous !== null && result.items.some((item) => item.id === previous)
          ? previous
          : (result.items[0]?.id ?? null),
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Network list failed to load. Check the connection and retry.",
      });
    }
  }, [sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const selected =
    state.kind === "ready"
      ? (state.items.find((item) => item.id === selectedNetworkId) ?? null)
      : null;

  return (
    <div>
      <h2 className="xi-page-title" data-testid="networks-heading" id="networks-heading">
        {t("modelNetworks")}</h2>
      <p className="xi-hint">
        
        {t("immutableVersionAdministrationNoDraftNetworkIs")}
      </p>
      <ImportForm onImported={() => void reload()} />
      <section className="xi-card" aria-labelledby="network-list-heading">
        <h3 className="xi-section-title" id="network-list-heading">
          {t("networks")}{state.kind === "ready" && ` (${state.total})`}
        </h3>
        {state.kind === "loading" && <p>{t("loadingNetworks")}</p>}
        {state.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {uiText(state.message)}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              {t("retry")}</button>
          </div>
        )}
        {state.kind === "ready" &&
          (state.items.length === 0 ? (
            <p>{t("noNetworksYetImportXmlAboveTo")}</p>
          ) : (
            <table
              className="xi-table"
              aria-label={t("networks")}
              data-testid="networks-list"
              id="networks-list"
            >
              <thead>
                <tr>
                  <th scope="col">{t("name")}</th>
                  <th scope="col">{t("versions")}</th>
                  <th scope="col">{t("actions")}</th>
                </tr>
              </thead>
              <tbody>
                {state.items.map((network) => (
                  <tr key={network.id}>
                    <td>{network.name}</td>
                    <td>{network.version_count}</td>
                    <td>
                      <button
                        className="xi-btn xi-btn-secondary"
                        type="button"
                        onClick={() => setSelectedNetworkId(network.id)}
                      >
                        {network.id === selectedNetworkId ? t("selected") : t("select")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>
      {selected !== null && (
        <section className="xi-card" aria-label={t("versions")}>
          <VersionHistory key={selected.id} network={selected} />
        </section>
      )}
    </div>
  );
}
