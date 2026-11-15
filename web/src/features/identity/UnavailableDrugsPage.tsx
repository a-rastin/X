import { useEffect, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { CheckboxList, CheckboxListItem } from "@astryxdesign/core/CheckboxList";
import { VStack } from "@astryxdesign/core/VStack";
import "./unavailable-drugs.css";
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
    <VStack as="section" className="unavailable-drugs" gap={5}>
      <header>
        <h2 className="xi-page-title">{n("unavailableDrugs")}</h2>
        <p className="unavailable-drugs-intro">{n("availabilityHelp")}</p>
      </header>
      {settings ? (
        <VStack gap={4}>
          {[...new Set(settings.catalog.map((d) => d.group))].map((group) => {
            const drugs = settings.catalog.filter((d) => d.group === group);
            const ids = drugs.map((d) => d.id);
            return (
              <section className="xi-card unavailable-drugs-group" key={group}>
                <CheckboxList
                  label={n(group === "lai" ? "laiGroup" : group)}
                  value={selected.filter((id) => ids.includes(id))}
                  onChange={(values) => {
                    setSelected((current) => [...current.filter((id) => !ids.includes(id)), ...values]);
                    setStatus("");
                  }}
                  hasDividers
                >
                  {drugs.map((d) => (
                    <CheckboxListItem key={d.id} value={d.id} label={<bdi>{d.label ?? d.name ?? d.id}</bdi>} />
                  ))}
                </CheckboxList>
              </section>
            );
          })}
        </VStack>
      ) : !status ? (
        <p role="status">{n("loading")}</p>
      ) : null}
      <VStack className="xi-card unavailable-drugs-footer" gap={3}>
        <p>{n("availabilityScope")}</p>
        <div className="nc-actions">
          <Button label={n("clear")} isDisabled={!settings || busy} onClick={() => { setSelected([]); setStatus(""); }} />
          <Button label={n("save")} variant="primary" isDisabled={!settings} isLoading={busy} onClick={() => void save()} />
        </div>
        {status && <p role="status">{n(status)}</p>}
        {conflict && (
          <div role="alert">
            <p>{n("planConflict")}</p>
            <div className="nc-actions">
              <Button label={n("reload")} onClick={() => { setSelected(settings!.selected_ids); setConflict(false); setStatus(""); }} />
              <Button label={n("reconcile")} onClick={() => void save()} />
            </div>
          </div>
        )}
      </VStack>
    </VStack>
  );
}
