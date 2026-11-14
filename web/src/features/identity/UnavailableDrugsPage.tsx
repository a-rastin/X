import { useEffect, useState } from "react";
import { ApiError, newIdempotencyKey, request } from "./api";
import { useAuth } from "./auth";
import { useNewCaseText } from "../new-case/NewCaseSections";
type Settings = {
  catalog: { id: string; name?: string; label?: string; group: string }[];
  selected_ids: string[];
  revision: number;
};
export function UnavailableDrugsPage() {
  const n = useNewCaseText();
  const { user, sessionExpired } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void request<Settings>("/me/unavailable-drugs")
      .then((s) => {
        if (!cancelled) {
          setSettings(s);
          setSelected(s.selected_ids);
        }
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) sessionExpired();
        else setStatus("failed");
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const save = async () => {
    if (!settings) return;
    setBusy(true);
    try {
      const s = await request<Settings>("/me/unavailable-drugs", {
        method: "PATCH",
        csrf: true,
        body: { selected_ids: selected },
        headers: {
          "If-Match": `"${settings.revision}"`,
          "Idempotency-Key": newIdempotencyKey(),
        },
      });
      setSettings(s);
      setSelected(s.selected_ids);
      setStatus("saved");
      setConflict(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) {
        const s = await request<Settings>("/me/unavailable-drugs");
        setSettings(s);
        setConflict(true);
      } else if (e instanceof ApiError && e.status === 401) sessionExpired();
      setStatus("failed");
    } finally {
      setBusy(false);
    }
  };
  if (user?.role !== "physician") return null;
  return (
    <section className="xi-card">
      <h2>{n("unavailableDrugs")}</h2>
      {settings ? (
        [...new Set(settings.catalog.map((d) => d.group))].map((group) => (
          <fieldset key={group}>
            <legend>{n(group === "lai" ? "laiGroup" : group)}</legend>
            {settings.catalog
              .filter((d) => d.group === group)
              .map((d) => (
                <label className="nc-choice" key={d.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(d.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, d.id]
                          : selected.filter((id) => id !== d.id),
                      )
                    }
                  />
                  <bdi>{d.label ?? d.name ?? d.id}</bdi>
                </label>
              ))}
          </fieldset>
        ))
      ) : (
        <p>{n("loading")}</p>
      )}
      <div className="nc-actions">
        <button
          className="xi-btn xi-btn-secondary"
          onClick={() => setSelected([])}
        >
          {n("clear")}
        </button>
        <button
          className="xi-btn xi-btn-primary"
          disabled={busy || !settings}
          onClick={() => void save()}
        >
          {n("save")}
        </button>
      </div>
      {status && <p role="status">{n(status)}</p>}
      {conflict && (
        <div role="alert">
          <p>{n("planConflict")}</p>
          <button
            onClick={() => {
              setSelected(settings!.selected_ids);
              setConflict(false);
            }}
          >
            {n("reload")}
          </button>
          <button onClick={() => void save()}>{n("reconcile")}</button>
        </div>
      )}
    </section>
  );
}
