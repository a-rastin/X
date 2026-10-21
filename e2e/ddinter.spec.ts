import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { uniqueName, uniquePatientId, uniqueLetters, ensurePhysician, physicianSession, loginAs, acknowledgeWarning } from "./helpers";

// T9 + T1 acceptance over real PostgreSQL. Seed e2e/ddinter_seed.py first.
// The catalog has the requested 1,939 IDs but deliberately synthetic assertions.
test.setTimeout(90000);
const fixtureFile = "/tmp/ddinter-browser-fixtures/versions.json";
test.skip(!existsSync(fixtureFile), "Seed the isolated synthetic browser database with e2e/ddinter_seed.py first.");
const versions = (existsSync(fixtureFile) ? JSON.parse(readFileSync(fixtureFile, "utf8")) : {}) as { first: string; legacy: string };

async function setup(page: Page, request: APIRequestContext) {
  const credentials = { username: uniqueName("ddinter"), password: "secret123" };
  await ensurePhysician(request, credentials.username, credentials.password);
  const csrf = await physicianSession(request, credentials);
  const response = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": uniqueName("patient") },
    data: { identifier: uniquePatientId(), given_name: uniqueLetters("given"), family_name: uniqueLetters("family"), sex: "F", age: 30, clinical_status: "first_time" },
  });
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json();
  await loginAs(page, "physician", credentials.username, credentials.password);
  await acknowledgeWarning(page);
  return { id: body.draft.id as string, patientId: body.patient.id as string, csrf };
}

async function open(page: Page, id: string) {
  await page.goto(`/#/encounters/${id}#medications`);
  await expect(page.locator("#meds-catalog-info")).toBeVisible();
  await expect(page.locator("#meds-save")).toBeEnabled();
}

async function strict(request: APIRequestContext, id: string, csrf: string, ids: string[], dataset = versions.first) {
  const preview = await request.get(`/api/v1/encounters/${id}/medications`);
  const response = await request.post(`/api/v1/encounters/${id}/medications`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${(await preview.json()).revision}"`, "Idempotency-Key": uniqueName("meds") },
    data: { medications: ids.map(catalog_drug_id => ({ catalog_drug_id })), dataset_version: dataset },
  });
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

async function select(page: Page, id: string) {
  await page.locator("#meds-search").fill(id);
  const row = page.locator("#meds-search-results li").filter({ hasText: `(${id})` });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: /^Add / }).click();
}

test("native severities, absent pairs, duplicate/order invariance and escaped repeated CSV provenance", async ({ page, request }) => {
  const { id, csrf } = await setup(page, request);
  const ids = ["DDInter1", "DDInter2", "DDInter3", "DDInter4", "DDInter5"];
  const saved = await strict(request, id, csrf, [...ids, "DDInter1"]);
  await open(page, id);
  const report = page.locator("#ddi-report");
  await expect(report).toBeVisible();
  for (const severity of ["Major", "Moderate", "Minor", "Unknown"]) await expect(report).toContainText(`highest known severity: ${severity}`);
  await expect(report).toContainText("Mechanism and management details were not supplied");
  await expect(report).toContainText("Coverage unavailable");
  await expect(report).not.toContainText("Conflicting severities");
  await expect(report).not.toContainText("Contraindicated");
  const pair = page.locator("#ddi-report-pair-0");
  await expect(pair).toContainText("not independent clinical corroboration");
  const details = pair.locator("details");
  await expect(details).not.toHaveAttribute("open", "");
  await details.locator("summary").click();
  await expect(details.locator("pre")).toHaveCount(2);
  await expect(details).toContainText("DDInterID_A");
  await expect(details).toContainText("ddinter_downloads_code_A.csv");
  await expect(details).toContainText("ddinter_downloads_code_B.csv");
  await expect(details).toContainText("record 1");
  await expect(details).toContainText("<script>alert(1)</script>");
  await expect(report.locator("script, b")).toHaveCount(0);
  const first = await (await request.get(`/api/v1/encounters/${id}/ddi-report`)).json();
  expect(first.report.pairs).toHaveLength(10);
  expect(first.report.pairs.filter((p: { drug_a: string; drug_b: string }) => p.drug_a === p.drug_b)).toHaveLength(0);
  const reversed = await strict(request, id, csrf, [...ids].reverse());
  expect(reversed.medication_fingerprint).toBe(saved.medication_fingerprint);
  const second = await (await request.get(`/api/v1/encounters/${id}/ddi-report`)).json();
  expect(second.report.pairs).toEqual(first.report.pairs);
});

test("zero and one medication keep severity-only and missing-coverage warnings visible", async ({ page, request }) => {
  const { id, csrf } = await setup(page, request);
  for (const ids of [[], ["DDInter1939"]]) {
    await strict(request, id, csrf, ids);
    await open(page, id);
    await expect(page.locator("#ddi-report")).toContainText("No pairs to check");
    await expect(page.locator("#ddi-report")).toContainText("Mechanism and management details were not supplied");
    await expect(page.locator("#ddi-report-limitations")).toContainText("missing pairs have unavailable coverage");
  }
});

test("pagination reaches every ID and selections beyond 100 and 1000 retain names after autosave, strict save and reload", async ({ page, request }) => {
  test.setTimeout(240000);
  const { id } = await setup(page, request);
  await open(page, id);
  await expect(page.locator("#meds-catalog-info")).toContainText("1939 drugs");
  const found = new Set<string>();
  for (let offset = 0; offset < 1939; offset += 25) {
    await expect(page.getByLabel("Catalog pagination")).toContainText(`${offset + 1}–${Math.min(offset + 25, 1939)} of 1939`);
    for (const text of await page.locator("#meds-search-results li").allTextContents()) {
      const match = text.match(/\((DDInter\d+)\)/);
      expect(match).not.toBeNull();
      expect(found.has(match![1])).toBe(false);
      found.add(match![1]);
    }
    if (offset + 25 < 1939) await page.locator("#meds-next").click();
  }
  expect(found.size).toBe(1939);
  await expect(page.locator("#meds-next")).toBeDisabled();
  await select(page, "DDInter101");
  await select(page, "DDInter1001");
  await select(page, "DDInter1939");
  await expect(page.getByLabel("Catalog pagination")).toContainText("1–1 of 1");
  // Wait for the structural autosave, then prove the first strict pin remains available.
  await expect.poll(async () => (await (await request.get(`/api/v1/encounters/${id}/medications`)).json()).entries.length).toBe(3);
  await expect(page.locator("#ddi-pending")).toBeVisible();
  await expect(page.locator("#meds-save")).toBeEnabled();
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report")).toBeVisible();
  await page.reload();
  for (const name of ["Drug 0101", "Drug 1001", "Drug 1939"]) await expect(page.locator("#meds-selected-list")).toContainText(name);
  await page.locator("#meds-remove-0").click();
  await expect(page.locator("#ddi-pending")).toContainText("changed");
  await expect(page.locator("#meds-save")).toBeEnabled();
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report")).toBeVisible();
});

test("late search response cannot replace a newer result", async ({ page, request }) => {
  const { id } = await setup(page, request);
  await open(page, id);
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  let started!: () => void;
  const pending = new Promise<void>(resolve => { started = resolve; });
  let finished!: () => void;
  const delivered = new Promise<void>(resolve => { finished = resolve; });
  await page.route("**/api/v1/drugs?*", async route => {
    if (new URL(route.request().url()).searchParams.get("query") === "Drug 0101") {
      const response = await route.fetch();
      started();
      await barrier;
      await route.fulfill({ response });
      finished();
    } else await route.continue();
  });
  await page.locator("#meds-search").fill("Drug 0101");
  await pending;
  await page.locator("#meds-search").fill("Drug 1001");
  await expect(page.locator("#meds-search-results")).toContainText("Drug 1001");
  release();
  await delivered;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator("#meds-search-results")).not.toContainText("Drug 0101");
  await expect(page.locator("#meds-search-results")).toContainText("Drug 1001");
});

test("lost reconcile response retries the identical body, release, revision and idempotency key", async ({ page, request }) => {
  const { id, patientId, csrf } = await setup(page, request);
  const discard = await request.post(`/api/v1/encounters/${id}/discard`, { headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' }, data: { confirm: true } });
  expect(discard.status()).toBe(200);
  const follow = await request.post(`/api/v1/patients/${patientId}/encounters`, { headers: { "X-CSRF-Token": csrf }, data: { kind: "follow_up", baseline: { history_values: {}, prior_scores: {}, medications: [{ catalog_drug_id: "DDInter1001" }], provenance_note: "Synthetic browser baseline" }, baseline_encounter_id: "synthetic-ddinter-baseline" } });
  expect(follow.status(), await follow.text()).toBe(201);
  const followId = (await follow.json()).encounter.id as string;
  await open(page, followId);
  await expect(page.locator("#ddi-pending")).toContainText("Reconciliation required");
  const attempts: { body: string | null; key: string; revision: string }[] = [];
  await page.route(`**/api/v1/encounters/${followId}/medications`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    const req = route.request();
    attempts.push({ body: req.postData(), key: req.headers()["idempotency-key"], revision: req.headers()["if-match"] });
    if (attempts.length === 1) {
      const response = await route.fetch();
      expect(response.status(), await response.text()).toBe(200);
      await route.abort("failed");
    } else await route.continue();
  });
  await page.locator("#meds-reconcile-action").click();
  await expect(page.locator("#meds-save-error")).toBeVisible();
  await page.getByRole("button", { name: "Retry save", exact: true }).click();
  await expect(page.locator("#ddi-report")).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  expect(JSON.parse(attempts[0].body!).reconciliation.status).toBe("reconciled");
  const saved = await (await request.get(`/api/v1/encounters/${followId}/medications`)).json();
  expect(saved.reconciliation.status).toBe("reconciled");
  expect(saved.revision).toBe(2);
});

test("proposal preserves CSV source metadata and still reads frozen monograph reports without optional fields", async ({ page, request }) => {
  const { id, csrf } = await setup(page, request);
  await strict(request, id, csrf, ["DDInter1", "DDInter2"]);
  const csv = (await (await request.get(`/api/v1/encounters/${id}/ddi-report`)).json()).report;
  const oldDraft = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": uniqueName("legacy") },
    data: { identifier: uniquePatientId(), given_name: uniqueLetters("given"), family_name: uniqueLetters("family"), sex: "F", age: 30, clinical_status: "first_time" },
  });
  expect(oldDraft.status(), await oldDraft.text()).toBe(201);
  const legacyId = (await oldDraft.json()).draft.id as string;
  await strict(request, legacyId, csrf, ["catalog_e2e_alpha", "catalog_e2e_beta"], versions.legacy);
  const legacy = (await (await request.get(`/api/v1/encounters/${legacyId}/ddi-report`)).json()).report;
  let report = csv;
  const batchId = "synthetic-frozen-report";
  const batch = { id: batchId, encounter_id: id, author_id: "synthetic", source_revision: 3, fingerprint: "synthetic", status: "ready", pinned_bundle: {}, created_at: "2026-10-20T00:00:00Z" };
  // Generation is a controlled HTTP fixture; the embedded reports are real checker responses.
  await page.route(`**/api/v1/generation-batches/${batchId}`, route => route.fulfill({ json: {
    batch, question_runs: [], baselines: [], jobs: [], job: null, attempts: 1,
    queue: { busy: false, leased_count: 0, queued_count: 0, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    freshness: { stale: false, reason: "current", current_fingerprint: "synthetic" },
    proposal: { id: "synthetic-proposal", batch_id: batchId, fingerprint: "synthetic", sections: [], skipped: [], coverage_warnings: [], ddi_report: report, created_at: "2026-10-20T00:00:00Z" },
    workflow: { complete: true, status: "complete", pending_question_keys: [], needs_clarification: [], skipped: [], coverage_warnings: [], ddi_status: "valid" },
  } }));
  await page.evaluate(({ id, batchId }) => localStorage.setItem(`xi.proposal.${id}`, JSON.stringify({ batchId, packages: [], sourceRevision: 3 })), { id, batchId });
  await page.goto(`/#/encounters/${id}#proposal-review`);
  const block = page.getByTestId("proposal-ddi");
  await expect(block).toContainText("DDInter supplied CSV exports");
  await expect(block).toContainText("Mechanism and management details were not supplied");
  await expect(block).toContainText("highest known severity: Major");
  await block.getByText("CSV provenance (2)").click();
  await expect(block).toContainText("DDInterID_A");
  await expect(block).toContainText("<script>alert(1)</script>");
  await expect(block.locator("script, b")).toHaveCount(0);
  report = legacy;
  await page.reload();
  await expect(block).toContainText(versions.legacy);
  await expect(block).toContainText("highest known severity: serious");
  await expect(block).not.toContainText("Mechanism and management details were not supplied");
  await block.getByText("Evidence assertions (2)").click();
  await expect(block).toContainText("E2eBeta increases the level");
  await expect(block).toContainText("Avoid combination");
});

test("concurrent publication changes the default while search, first pin and resumed search retain the original release", async ({ page, request }) => {
  const { id } = await setup(page, request);
  await open(page, id);
  // This test also works on reruns after the synthetic second release exists.
  const current = await (await request.get("/api/v1/drugs?query=Drug%201001")).json();
  const original = current.dataset_version as string;
  await select(page, "DDInter1001");
  const published = execFileSync("backend/.venv/bin/python", ["e2e/ddinter_seed.py", uniqueName("publication")], { env: { ...process.env, PYTHONPATH: "backend/src" }, encoding: "utf8" }).trim();
  const latest = await (await request.get("/api/v1/drugs?query=Drug%201001")).json();
  expect(latest.dataset_version).toBe(published);
  expect(latest.dataset_version).not.toBe(original);
  const posts: string[] = [];
  page.on("request", req => { if (req.method() === "POST" && req.url().endsWith(`/encounters/${id}/medications`)) posts.push(req.postData()!); });
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report-status")).toContainText(original);
  expect(JSON.parse(posts[0]).dataset_version).toBe(original);
  await page.reload();
  await expect(page.locator("#meds-catalog-info")).toContainText(original);
  await select(page, "DDInter1939");
  await expect(page.locator("#meds-catalog-info")).toContainText(original);
});

test("unpinned autosaved selections retain their release and names across publication, reload and step reentry before first strict save", async ({ page, request }) => {
  const { id } = await setup(page, request);
  const catalog = await (await request.get("/api/v1/drugs?query=Drug%201001")).json();
  const original = catalog.dataset_version as string;
  const firstName = catalog.items.find((item: { catalog_drug_id: string }) => item.catalog_drug_id === "DDInter1001").canonical_name as string;
  const otherCatalog = await (await request.get(`/api/v1/drugs?query=DDInter1939&dataset_version=${original}`)).json();
  const otherName = otherCatalog.items.find((item: { catalog_drug_id: string }) => item.catalog_drug_id === "DDInter1939").canonical_name as string;
  await open(page, id);
  await expect(page.locator("#meds-catalog-info")).toContainText(original);
  await select(page, "DDInter1001");
  await select(page, "DDInter1939");
  const preview = async () => (await (await request.get(`/api/v1/encounters/${id}/medications`)).json());
  await expect.poll(async () => {
    const state = await preview();
    return { association: state.selection_dataset_version, count: state.entries.length, pin: state.dataset_version, fingerprint: state.medication_fingerprint };
  }).toEqual({ association: original, count: 2, pin: null, fingerprint: null });
  const published = execFileSync("backend/.venv/bin/python", ["e2e/ddinter_seed.py", uniqueName("resume")], { env: { ...process.env, PYTHONPATH: "backend/src" }, encoding: "utf8" }).trim();
  const latest = await (await request.get("/api/v1/drugs?query=Drug%201001")).json();
  expect(latest.dataset_version).toBe(published);
  expect(published).not.toBe(original);
  const changedName = latest.items.find((item: { catalog_drug_id: string }) => item.catalog_drug_id === "DDInter1001").canonical_name as string;
  expect(changedName).not.toBe(firstName);
  const searches: string[] = [];
  page.on("request", req => {
    const url = new URL(req.url());
    if (url.pathname === "/api/v1/drugs") searches.push(url.searchParams.get("dataset_version") ?? "missing");
  });
  await page.reload();
  await expect(page.locator("#meds-catalog-info")).toContainText(original);
  await expect(page.locator("#meds-selected-list")).toContainText(firstName);
  await expect(page.locator("#meds-selected-list")).toContainText(otherName);
  await expect(page.locator("#meds-selected-list")).not.toContainText(changedName);
  await expect(page.locator("#ddi-pending")).toBeVisible();
  await expect(page.locator("#ddi-report")).toHaveCount(0);
  await page.locator("#meds-search").fill("DDInter1001");
  await expect(page.locator("#meds-search-results")).toContainText(firstName);
  await page.locator("#wizard-step-history").click();
  await expect(page.locator("#medications-heading")).toHaveCount(0);
  await page.locator("#wizard-step-medications").click();
  await expect(page.locator("#meds-catalog-info")).toContainText(original);
  await expect(page.locator("#meds-selected-list")).toContainText(firstName);
  await expect(page.locator("#meds-selected-list")).toContainText(otherName);
  await expect(page.locator("#meds-save")).toBeEnabled();
  expect((await preview()).dataset_version).toBeNull();
  expect(searches.length).toBeGreaterThan(0);
  expect(searches.every(version => version === original)).toBe(true);
  const posts: string[] = [];
  page.on("request", req => { if (req.method() === "POST" && req.url().endsWith(`/encounters/${id}/medications`)) posts.push(req.postData()!); });
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report-status")).toContainText(original);
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0]).dataset_version).toBe(original);
  const saved = await preview();
  expect(saved.dataset_version).toBe(original);
  expect(saved.selection_dataset_version).toBe(original);
  expect(saved.medication_fingerprint).toMatch(/^[a-f0-9]{64}$/);
});
