import { test, expect } from "@playwright/test";
import {
  uniqueName,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

/* S24 model version administration (seam T9 browser; strict validation,
 * pointer revision fencing and authorization covered by
 * BT/http/test_networks.py and not duplicated here).
 *
 * Selector contract (see NetworksPage.tsx / GraphView.tsx headers):
 * - heading `#networks-heading` ("Model networks")
 * - import form `#networks-import-form` (`#import-name`, `#import-xml`,
 *   `#import-reviewer`, `#import-decision`, `#import-date`)
 * - network list `#networks-list`
 * - version history `#networks-versions`
 * - validation report `#networks-validation`
 * - graph `#networks-graph` (read-only SVG + ordered lists, no edit ops)
 * - activation panel `#networks-activation` (`#bundle-workflow`,
 *   `#bundle-reviewer`, `#bundle-decision`, `#bundle-date`,
 *   `#bundle-revision`)
 *
 * Synthetic XML only (A→B, no BNs/ medical sources).
 */

function validXml(): string {
  return (
    '<BIF VERSION="0.3"><NETWORK><NAME>Valid</NAME>' +
    "<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
    "<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
    "<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>" +
    "<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>" +
    "<TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>" +
    "</NETWORK></BIF>"
  );
}

function editedXml(): string {
  return (
    '<BIF VERSION="0.3"><NETWORK><NAME>Valid</NAME>' +
    "<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
    "<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
    "<DEFINITION><FOR>A</FOR><TABLE>0.7 0.3</TABLE></DEFINITION>" +
    "<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>" +
    "<TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>" +
    "</NETWORK></BIF>"
  );
}

async function gotoNetworks(page: import("@playwright/test").Page): Promise<void> {
  await page.getByRole("link", { name: "Networks" }).click();
  await expect(page.locator("#networks-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#networks-import-form")).toBeVisible();
  // Empty dev DB shows "No networks yet" without #networks-list; the list
  // appears after the first import, so assert it after importing instead.
}

test("admin import → inspect → version → validate → activate → stale denial → rollback", async ({
  page,
}) => {
  const networkName = uniqueName("e2enet");
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await gotoNetworks(page);

  // Import v1 as approved synthetic XML.
  const importForm = page.locator("#networks-import-form");
  await page.locator("#import-name").fill(networkName);
  await page.locator("#import-xml").fill(validXml());
  await page.locator("#import-reviewer").fill("owner");
  await page.locator("#import-decision").selectOption("approved");
  await page.locator("#import-date").fill("2026-10-04");
  await importForm.getByRole("button", { name: "Import network" }).click();
  await expect(
    importForm.getByText(`Imported ${networkName} as version 1.`),
  ).toBeVisible({ timeout: 15000 });

  // Select our network (parallel runs share the dev DB).
  const networksTable = page.getByRole("table", { name: "Networks" });
  const row = networksTable.getByRole("row", { name: new RegExp(networkName) });
  await expect(row).toBeVisible({ timeout: 15000 });
  await row.getByRole("button", { name: /Select/ }).click();

  const versions = page.locator("#networks-versions");
  await expect(versions).toBeVisible({ timeout: 15000 });
  await expect(versions).toContainText("v1");

  // Inspect v1 graph: ordered nodes/edges/readable states + validation.
  const graph = page.locator("#networks-graph");
  await expect(graph).toBeVisible({ timeout: 15000 });
  await expect(graph).toContainText("A → B");
  await expect(graph).toContainText("no");
  await expect(graph).toContainText("yes");
  await expect(graph).toContainText("XSD structurally valid");
  await expect(graph).toContainText("Semantics valid");
  await expect(graph.locator("svg")).toBeVisible();
  await expect(graph).toContainText("read only", { ignoreCase: true });
  await expect(graph.locator("button")).toHaveCount(0);
  await expect(graph.locator("input")).toHaveCount(0);

  // Validation details stay structural/pipeline-only, never clinical.
  const validation = page.locator("#networks-validation");
  await expect(validation).toBeVisible({ timeout: 15000 });
  await expect(validation).toContainText("Structural (XSD)");
  await expect(validation).toContainText("Semantic");
  await expect(validation).toContainText("XSD structurally valid");
  await expect(validation).toContainText("Semantics valid");
  await expect(validation).not.toContainText("clinically valid", {
    ignoreCase: true,
  });

  // New immutable version (edit as new row, 70/30 roots).
  const versionForm = page.locator(
    "#networks-versions form[aria-label='Create version']",
  );
  await versionForm.getByLabel("XMLBIF XML").fill(editedXml());
  await versionForm.getByLabel("Reviewer").fill("owner");
  await versionForm.getByLabel("Review decision").selectOption("approved");
  await versionForm.getByLabel("Review date").fill("2026-10-04");
  await versionForm.getByRole("button", { name: "Save as new version" }).click();
  // Commit-visibility race (same as helpers.ensurePatient): the version row
  // can land after the first list fetch, so reload once before asserting v2.
  try {
    await expect(versions).toContainText("v2", { timeout: 8000 });
  } catch {
    await page.reload();
    await expect(page.locator("#networks-heading")).toBeVisible({
      timeout: 15000,
    });
    const retryRow = page
      .getByRole("table", { name: "Networks" })
      .getByRole("row", { name: new RegExp(networkName) });
    await expect(retryRow).toBeVisible({ timeout: 15000 });
    await retryRow.getByRole("button", { name: /Select/ }).click();
    await expect(page.locator("#networks-versions")).toContainText("v2", {
      timeout: 15000,
    });
  }

  // Inspect v2: ordered graph still A→B, activation targets v2.
  const versionsTable = page.getByRole("table", {
    name: new RegExp(`Versions of ${networkName}`),
  });
  const v2Row = versionsTable.getByRole("row", { name: /v2/ });
  await expect(v2Row).toBeVisible();
  await v2Row.getByRole("button", { name: "Inspect" }).click();
  let activation = page.locator("#networks-activation");
  await expect(activation).toContainText("version 2", { timeout: 15000 });
  await expect(page.locator("#networks-graph")).toContainText("A → B");

  // Activate v2 with no precondition → next global revision.
  await activation.getByLabel("Reviewer").fill("owner");
  await activation.getByLabel("Review decision").selectOption("approved");
  await activation.getByLabel("Review date").fill("2026-10-04");
  await activation.getByLabel(/Expected pointer revision/).fill("");
  await activation.getByRole("button", { name: "Activate…" }).click();
  await expect(page.getByRole("alertdialog")).toContainText(
    "Confirm activation",
  );
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Confirm activation" })
    .click();
  await expect(activation).toContainText(/Pointer revision \d+/, {
    timeout: 15000,
  });
  const activatedText = (await activation.textContent()) ?? "";
  const revMatch = activatedText.match(/Pointer revision (\d+)/);
  expect(revMatch).not.toBeNull();
  const liveRevision = revMatch ? revMatch[1] : "1";

  // Stale activation denial: a far-future If-Match never equals liveRevision.
  await activation.getByLabel(/Expected pointer revision/).fill("99999");
  await activation.getByRole("button", { name: "Activate…" }).click();
  await expect(page.getByRole("alertdialog")).toContainText(
    "Confirm activation",
  );
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Confirm activation" })
    .click();
  await expect(activation).toContainText(/changed|moved/i, { timeout: 15000 });

  // Prior version stays retrievable: inspect v1, graph still A→B.
  const v1Row = versionsTable.getByRole("row", { name: /v1/ });
  await expect(v1Row).toBeVisible();
  await v1Row.getByRole("button", { name: "Inspect" }).click();
  activation = page.locator("#networks-activation");
  await expect(activation).toContainText("version 1", { timeout: 15000 });
  await expect(page.locator("#networks-graph")).toContainText("A → B");

  // Roll back to v1 with the fresh revision → liveRevision + 1.
  await activation.getByLabel("Reviewer").fill("owner");
  await activation.getByLabel("Review decision").selectOption("approved");
  await activation.getByLabel("Review date").fill("2026-10-04");
  await activation.getByLabel(/Expected pointer revision/).fill(liveRevision);
  await activation.getByRole("button", { name: "Roll back…" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Confirm rollback");
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Confirm rollback" })
    .click();
  const nextRevision = String(Number(liveRevision) + 1);
  await expect(activation).toContainText(`Pointer revision ${nextRevision}`, {
    timeout: 15000,
  });
});

test("physician denied + unauth direct checks", async ({ page, request }) => {
  // Unauthenticated API first: the request jar is still clean in this test.
  const unauthList = await request.get("/api/v1/networks");
  expect(unauthList.status()).toBe(401);
  const unauthRollback = await request.post("/api/v1/model-bundles/rollback", {
    data: {
      workflow: "registration",
      selections: [
        {
          network_id: "00000000-0000-0000-0000-000000000000",
          version_id: "00000000-0000-0000-0000-000000000000",
        },
      ],
      review: { reviewer: "owner", decision: "approved", date: "2026-10-04" },
    },
  });
  expect(unauthRollback.status()).toBe(401);

  // Unauthenticated browser degrades to sign-in.
  await page.goto("/#/networks");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  // Physician direct API: denied on list and rollback (auth first, never 404).
  const username = uniqueName("e2enetphys");
  // Dev-DB commit-visibility race (see helpers.ensurePhysician): a login 200
  // can precede its session row, so retry setup with a short settle delay.
  let created = false;
  for (let attempt = 0; attempt < 3 && !created; attempt += 1) {
    try {
      // eslint-disable-next-line no-await-in-loop
      await ensurePhysician(request, username, "secret123");
      created = true;
    } catch {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  if (!created) {
    await ensurePhysician(request, username, "secret123");
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
  let csrf = await physicianSession(request, {
    username,
    password: "secret123",
  });
  let deniedList = await request.get("/api/v1/networks");
  for (let attempt = 0; attempt < 3 && deniedList.status() === 401; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    csrf = await physicianSession(request, {
      username,
      password: "secret123",
    });
    deniedList = await request.get("/api/v1/networks");
  }
  expect(deniedList.status()).toBe(403);
  const deniedRollback = await request.post(
    "/api/v1/model-bundles/rollback",
    {
      headers: { "X-CSRF-Token": csrf },
      data: {
        workflow: "registration",
        selections: [
          {
            network_id: "00000000-0000-0000-0000-000000000000",
            version_id: "00000000-0000-0000-0000-000000000000",
          },
        ],
        review: { reviewer: "owner", decision: "approved", date: "2026-10-04" },
      },
    },
  );
  expect(deniedRollback.status()).toBe(403);

  // Physician browser: no governance navigation, route guard only.
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);
  await expect(page.getByRole("link", { name: "Networks" })).toHaveCount(0);
  await page.goto("/#/networks");
  await expect(
    page.getByText("Administrator access required."),
  ).toBeVisible();
});
