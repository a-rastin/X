import { test, expect } from "@playwright/test";
import { execFile } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
  signOut,
} from "./helpers";

/* S53 export lists + printable longitudinal patient reports (seam T9).
 *
 * Backend (read-only): `BT/http/test_exports.py` (14 T1 tests) owns
 * admin-only `GET /exports/patients.csv`, `/exports/physicians.csv`,
 * `GET /patients/{id}/report` (401/403 gates, stable headers/UTF-8/exact
 * identifier bytes, formula neutralization, quoting, escaped signed HTML
 * with original+accepted CPTs/versions/indicators/attribution, private
 * drafts excluded, `private, no-store`, export/report audit). No
 * backend/src changes here.
 *
 * Frontend (read-only): `web/src/features/admin/exports/api.ts` +
 * `ExportsPage.tsx` (testids exports-heading/patients/physicians/status)
 * + `ReportPage.tsx` (testids report-heading/research-label/frame/print/
 * status, sandboxed iframe srcDoc, never dangerouslySetInnerHTML) mounted
 * via `#/exports` + `#/patients/:id/report` guards in app/pages.tsx
 * (complements to the server 403) + `#chart-report-link` (admin chart
 * only). No web/src changes here.
 *
 * Constraints: real endpoints only — NO page.route mocks anywhere in this
 * spec. CSV downloads and the report document come from the live backend.
 * The signed multi-encounter fixture is REAL signed data: a helper script
 * (written to os.tmpdir() at runtime, never the repo) drives the public
 * pipeline against the dev database — DDI publish, registration
 * history/meds/generation-batch, worker run_once() with the deterministic
 * provider endpoint (original generation) plus stub drains (local
 * recalculation), a real CPT adjustment, exact acceptance, two atomic
 * signs (registration + follow-up), an any-physician addendum, and a
 * frozen note — all through public HTTP except the in-process worker
 * steps, which are the same production run_once() entry point the T1 suite
 * proves. Synthetic two-node A->B content only; expected literals are
 * worked fixtures (question keys, indicator copy, evil strings).
 *
 * The helper needs DATABASE_URL for the dev database in the environment
 * (source the repo .env before running: `set -a; . ./.env; set +a`), and
 * refuses anything pointing at the test database. The backend dev server
 * (:8000) must be running. Run with --workers=1 when the shared dev DB is
 * under parallel pressure.
 *
 * Selector contract: exports-heading, exports-patients, exports-physicians,
 * exports-status, report-heading, report-research-label, report-frame,
 * report-print, report-status, chart-report-link.
 */

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const VENV_PYTHON = join(REPO_ROOT, "backend", ".venv", "bin", "python");
const API_BASE = "http://127.0.0.1:8000";

const EVIL_PLAN = "<script>alert('plan-xss')</script> Keep monitoring monthly.";
const EVIL_NOTE = "<img src=x onerror=alert('note-xss')> Proposal looks complete.";
const EVIL_ADDENDUM = "Correction <b>bold</b> & <i>italic</i> with link text.";

interface SignedFixture {
  username: string;
  password: string;
  otherUsername: string;
  patientId: string;
  identifier: string;
  encounterId: string;
  followupId: string;
}

function helperSource(): string {
  const planLit = JSON.stringify(EVIL_PLAN);
  const noteLit = JSON.stringify(EVIL_NOTE);
  const addendumLit = JSON.stringify(EVIL_ADDENDUM);
  const scriptLines = [
    "import http.cookiejar",
    "import json",
    "import os",
    "import sys",
    "import urllib.request",
    "from dataclasses import asdict",
    "from datetime import timedelta",
    "from pathlib import Path",
    "",
    "API = sys.argv[1]",
    "PHYSICIAN = sys.argv[2]",
    "PASSWORD = sys.argv[3]",
    "OTHER = sys.argv[4]",
    "IDENTIFIER = sys.argv[5]",
    "ROOT = Path(sys.argv[6])",
    "sys.path.insert(0, str(ROOT / 'backend' / 'src'))",
    "",
    "PLAN_EVIL = " + planLit,
    "NOTE_EVIL = " + noteLit,
    "ADDENDUM_EVIL = " + addendumLit,
    "",
    "from x_insight import contracts, db as db_module",
    "",
    "DB_URL = os.environ.get('DATABASE_URL', '')",
    "assert DB_URL and 'x_insight_test' not in DB_URL, 'DATABASE_URL must target the dev database'",
    "ENGINE = db_module.build_engine(DB_URL)",
    "",
    "jar = http.cookiejar.CookieJar()",
    "opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))",
    "",
    "",
    "def call(method, path, body=None, headers=None):",
    "    data = None",
    "    heads = {'Content-Type': 'application/json'}",
    "    heads.update(headers or {})",
    "    if body is not None:",
    "        data = json.dumps(body).encode('utf-8')",
    "    req = urllib.request.Request(API + path, data=data, headers=heads, method=method)",
    "    try:",
    "        with opener.open(req) as res:",
    "            raw = res.read().decode('utf-8')",
    "            return res.status, (json.loads(raw) if raw else {})",
    "    except urllib.error.HTTPError as exc:",
    "        return exc.code, exc.read().decode('utf-8')",
    "",
    "",
    "def need(status, body, want, label):",
    "    assert status == want, label + ' got ' + str(status) + ' ' + str(body)[:300]",
    "    assert isinstance(body, dict), label + ' non-JSON body: ' + str(body)[:200]",
    "    return body",
    "",
    "ETAG = lambda rev: chr(34) + str(rev) + chr(34)",
    "",
    "SNAP_JOBS = {}",
    "SNAP_FAIR = {}",
    "SNAP_ATTEMPTS = set()",
    "def snapshot_queue():",
    "    global SNAP_FAIR, SNAP_ATTEMPTS",
    "    from sqlalchemy import text as _text",
    "    with ENGINE.connect() as _c:",
    "        for _r in _c.execute(_text('SELECT id, status, attempt_index, next_eligible_at FROM reasoning_jobs')).all():",
    "            _next = _r[3].isoformat() if _r[3] is not None else None",
    "            SNAP_JOBS[str(_r[0])] = (str(_r[1]), int(_r[2]), _next)",
    "        for _f in _c.execute(_text('SELECT job_class, author_id, last_granted_at FROM reasoning_fairness')).all():",
    "            _last = _f[2].isoformat() if _f[2] is not None else None",
    "            SNAP_FAIR[(str(_f[0]), str(_f[1]))] = _last",
    "        for _a in _c.execute(_text('SELECT id FROM reasoning_job_attempts')).all():",
    "            SNAP_ATTEMPTS.add(str(_a[0]))",
    "    print('QUEUE-SNAPSHOT jobs=' + str(len(SNAP_JOBS)) + ' fairness=' + str(len(SNAP_FAIR)) + ' attempts=' + str(len(SNAP_ATTEMPTS)))",
    "def restore_queue():",
    "    from sqlalchemy import text as _text",
    "    with ENGINE.begin() as _c:",
    "        for _jid, _vals in SNAP_JOBS.items():",
    "            _c.execute(_text('UPDATE reasoning_jobs SET status=:s, attempt_index=:i, next_eligible_at=:n, lease_token=NULL, updated_at=NOW() WHERE id=:j'), {'s': _vals[0], 'i': _vals[1], 'n': _vals[2], 'j': _jid})",
    "        _rows = _c.execute(_text('SELECT id, job_id FROM reasoning_job_attempts')).all()",
    "        _del = [str(_a) for _a, _j in _rows if str(_j) in SNAP_JOBS and str(_a) not in SNAP_ATTEMPTS]",
    "        if _del:",
    "            _c.execute(_text('DELETE FROM reasoning_job_attempts WHERE id::text = ANY(:ids)'), {'ids': _del})",
    "        for (_cls, _auth), _last in SNAP_FAIR.items():",
    "            _c.execute(_text('UPDATE reasoning_fairness SET last_granted_at=:l WHERE job_class=:c AND author_id=:a'), {'l': _last, 'c': _cls, 'a': _auth})",
    "        _live = [(str(_r[0]), str(_r[1])) for _r in _c.execute(_text('SELECT job_class, author_id FROM reasoning_fairness')).all()]",
    "        for _pair in _live:",
    "            if _pair not in SNAP_FAIR:",
    "                _c.execute(_text('DELETE FROM reasoning_fairness WHERE job_class=:c AND author_id=:a'), {'c': _pair[0], 'a': _pair[1]})",
    "def batch_is_complete(batch_id):",
    "    _s, _b = call('GET', '/api/v1/generation-batches/' + batch_id)",
    "    return _s == 200 and isinstance(_b, dict) and _b.get('proposal') is not None and isinstance(_b.get('workflow'), dict) and _b['workflow'].get('complete') is True",
    "",
    "### RUN ###",
    "admin_login = need(*call('POST', '/api/v1/auth/login', {'username': 'admin', 'password': 'admin', 'role': 'admin'}), 200, 'admin login')",
    "AHEAD = {'X-CSRF-Token': admin_login['csrf_token']}",
    "need(*call('POST', '/api/v1/physicians', {'username': PHYSICIAN, 'password': PASSWORD}, AHEAD), 201, 'physician create')",
    "need(*call('POST', '/api/v1/physicians', {'username': OTHER, 'password': PASSWORD}, AHEAD), 201, 'other create')",
    "phys_login = need(*call('POST', '/api/v1/auth/login', {'username': PHYSICIAN, 'password': PASSWORD, 'role': 'physician'}), 200, 'physician login')",
    "PHEAD = {'X-CSRF-Token': phys_login['csrf_token']}",
    "snapshot_queue()",
    "",
    "from x_insight.ddi import publish as publish_module",
    "from x_insight.ddi.ingestion import build as build_module",
    "import tempfile",
    "tmp = Path(tempfile.mkdtemp(prefix='s53e2e'))",
    "sources = tmp / 'sources'",
    "sources.mkdir()",
    "for subject, names in (('Alpha', ['Beta']), ('Beta', ['Alpha'])):",
    "    entries = chr(10).join([n + chr(10) + n + ' raises levels. Avoid.' for n in names])",
    "    (sources / (subject + '.txt')).write_text('Interactions' + chr(10) + chr(10) + 'Contraindicated (0)' + chr(10) + chr(10) + 'Serious (1)' + chr(10) + chr(10) + entries + chr(10) + chr(10) + 'Monitor Closely (0)' + chr(10) + chr(10) + 'Minor (0)' + chr(10) + 'Warnings' + chr(10))",
    "concepts = [{'id': 'alpha', 'canonical_name': 'Alpha', 'concept_type': 'ingredient', 'catalog_drug_id': 'catalog_alpha', 'source': 's53 e2e'}, {'id': 'beta', 'canonical_name': 'Beta', 'concept_type': 'ingredient', 'catalog_drug_id': 'catalog_beta', 'source': 's53 e2e'}, {'id': 'gamma', 'canonical_name': 'Gamma', 'concept_type': 'ingredient', 'catalog_drug_id': 'catalog_gamma', 'source': 's53 e2e'}]",
    "dataset, report = build_module(sources, {'version': 's53-e2e/1', 'concepts': concepts, 'aliases': []})",
    "staging = tmp / 'staging'",
    "staging.mkdir()",
    "(staging / 'candidate_dataset.json').write_text(json.dumps(asdict(dataset), indent=2))",
    "(staging / 'report.json').write_text(json.dumps(asdict(report), indent=2))",
    "cand = json.loads((staging / 'candidate_dataset.json').read_text())",
    "reviewed = []",
    "for doc in cand['documents']:",
    "    if doc['source_path'] not in ('Alpha.txt', 'Beta.txt'):",
    "        continue",
    "    for entry in doc['entries']:",
    "        if entry['source_category'] in ('contraindicated', 'serious'):",
    "            reviewed.append({'source_path': doc['source_path'], 'span_start': entry['span_start'], 'span_end': entry['span_end'], 'severity': entry['source_category']})",
    "manifest = {'schema_version': 1, 'reviewer': 'owner', 'decision': 'approved_limited', 'date': '2026-10-06', 'record': 's53 e2e', 'parser_version': cand.get('parser_version'), 'terminology_version': cand.get('terminology_version'), 'terminology_checksum': cand.get('terminology_checksum'), 'coverage': {'scope': 'limited', 'excluded_sources': [{'path': 'Gamma.txt', 'reason': 'synthetic uncovered scope'}]}, 'corrections': [], 'reviewed_evidence': reviewed, 'limitations': ['synthetic limited scope: Gamma.txt excluded']}",
    "(tmp / 'manifest.json').write_text(json.dumps(manifest))",
    "try:",
    "    publish_module.publish_release(staging, tmp / 'manifest.json', ENGINE)",
    "except Exception as exc:",
    "    text = str(exc).lower()",
    "    assert 'unique' in text or 'duplicate' in text or 'already' in text or 'uq_' in text, 'DDI publish failed: ' + str(exc)[:300]",
    "",
    "XML = '<BIF VERSION=' + chr(39) + '0.3' + chr(39) + '><NETWORK><NAME>TwoNode</NAME>' + '<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>' + '<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>' + '<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>' + '<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>' + '</NETWORK></BIF>'",
    "import hashlib",
    "NETHASH = hashlib.sha256(XML.encode('utf-8')).hexdigest()",
    "",
    "",
    "def package(qkey, fa, fb):",
    "    rep = lambda s: s.replace('QKEY', qkey).replace('FAKEY', fa).replace('FBKEY', fb).replace('NETHASH', NETHASH).replace('NETXML', XML)",
    "    raw = '{\"manifest\": {\"schema_version\": \"question-package-v1\", \"question_key\": \"QKEY\", \"title\": \"Synthetic QKEY\", \"workflow\": \"registration\", \"version\": \"s53-e2e-v1\", \"review_status\": \"draft\", \"network_file\": \"network.xml\", \"network_hash\": \"NETHASH\", \"source_refs\": [\"synthetic/source.md\"], \"declared_node_order\": [\"A\", \"B\"], \"variables\": [{\"node_id\": \"A\", \"kind\": \"nature\", \"patient_value_type\": \"tristate\", \"states\": [\"no\", \"yes\"], \"ordered_parents\": []}, {\"node_id\": \"B\", \"kind\": \"nature\", \"patient_value_type\": \"tristate\", \"states\": [\"no\", \"yes\"], \"ordered_parents\": [\"A\"]}], \"patient_mappings\": [{\"node_id\": \"A\", \"allowed_source_paths\": [\"synthetic/history/FAKEY\"], \"transform\": \"copy\", \"time_window\": \"current_encounter\", \"usage\": \"cpt_context\", \"missing_policy\": \"needs_clarification\"}, {\"node_id\": \"B\", \"allowed_source_paths\": [\"synthetic/history/FBKEY\"], \"transform\": \"copy\", \"time_window\": \"current_encounter\", \"usage\": \"cpt_context\", \"missing_policy\": \"needs_clarification\"}], \"applicability\": {\"expression\": \"true\", \"required_fields\": [\"synthetic/history/FAKEY\", \"synthetic/history/FBKEY\"], \"unknown_policy\": \"needs_clarification\"}, \"cpt_contract\": {\"nodes\": [{\"node_id\": \"A\", \"parent_ids\": [], \"states\": [\"no\", \"yes\"]}, {\"node_id\": \"B\", \"parent_ids\": [\"A\"], \"states\": [\"no\", \"yes\"]}]}, \"query_nodes\": [\"A\", \"B\"], \"execution_evidence\": {}, \"prompt_version\": \"v1\", \"template_version\": \"v1\"}, \"prompt\": {\"version\": \"v1\", \"text\": \"Estimate every CPT in percentage units. Return strict schema.\"}, \"template\": {\"version\": \"v1\", \"branches\": [{\"when\": {\"node\": \"B\", \"state\": \"yes\", \"operator\": \"==\"}, \"text\": \"QKEY present: {B} done.\"}, {\"when\": {\"node\": \"B\", \"state\": \"no\", \"operator\": \"==\"}, \"text\": \"QKEY absent: {B} done.\"}]}, \"examples\": {\"numerical\": [{\"inputs\": {}, \"expected\": {\"A\": {\"no\": 0.8, \"yes\": 0.2}}}], \"clinical\": [{\"inputs\": {}, \"expected_for_review\": {\"B\": \"yes\"}, \"note\": \"review candidate\"}]}, \"review\": {\"reviewer\": \"owner\", \"decision\": \"draft\", \"date\": \"2026-10-04\", \"source_hashes\": {\"network.xml\": \"NETHASH\"}, \"assumptions\": [\"synthetic only\"], \"source_comparison\": \"synthetic\", \"explicit_graph\": \"A -> B\", \"reference_table_provenance\": \"synthetic\", \"estimation_instructions\": \"estimate every CPT\", \"result_mapping\": \"B yes/no\", \"numerical_examples\": \"two-node\", \"clinical_examples\": \"synthetic\", \"admission_measurements\": \"synthetic\", \"open_assumptions\": \"synthetic\"}, \"network_xml\": \"NETXML\"}'",
    "    return json.loads(rep(raw))",
    "",
    "CPT = {'network_hash': NETHASH, 'tables': [{'node_id': 'A', 'parent_ids': [], 'states': ['no', 'yes'], 'rows': [{'parent_states': [], 'percentages': ['80', '20']}]}, {'node_id': 'B', 'parent_ids': ['A'], 'states': ['no', 'yes'], 'rows': [{'parent_states': ['no'], 'percentages': ['90', '10']}, {'parent_states': ['yes'], 'percentages': ['30', '70']}]}]}",
    "",
    "from x_insight.reasoning import provider as provider_module",
    "from x_insight.reasoning import worker as worker_module",
    "",
    "",
    "def run_generation(batch_id):",
    "    endpoint = provider_module.DeterministicProviderEndpoint(script=[{'type': 'final', 'cpt': CPT}])",
    "    url = endpoint.start()",
    "    try:",
    "        config = provider_module.ProviderConfig(endpoint_url=url, model='s53-e2e', capability='schema', timeout_seconds=15.0)",
    "        adapter = provider_module.BoundedProviderAdapter(config, grant_token='', database_url=DB_URL)",
    "        moment = contracts.utcnow()",
    "        for _i in range(600):",
    "            outcome = worker_module.run_once(ENGINE, adapter, now=moment, database_url=DB_URL)",
    "            moment = moment + timedelta(seconds=1)",
    "            if batch_is_complete(batch_id):",
    "                print('BATCH-COMPLETE iterations=' + str(_i + 1) + ' outcome=' + str(outcome.get('status')))",
    "                return",
    "        raise AssertionError('batch never completed: ' + str(batch_id))",
    "    finally:",
    "        endpoint.stop()",
    "",
    "",
    "def drain_until(run_id, cap=40):",
    "    from x_insight.reasoning import provider as _pm",
    "    from x_insight.reasoning import worker as _wm",
    "    for _i in range(cap):",
    "        _wm.run_once(ENGINE, _pm.ControlledStubAdapter(mode='succeed'))",
    "        _s, _r = call('GET', '/api/v1/question-runs/' + run_id + '/review')",
    "        if _s == 200 and isinstance(_r, dict) and _r.get('calculation_state') == 'successfully_recalculated':",
    "            return _r",
    "    raise AssertionError('local calculation never succeeded for run ' + str(run_id))",
    "",
    "",
    "def accept_body(review):",
    "    baseline = review['baseline']",
    "    assert baseline is not None, 'no baseline to accept'",
    "    if review['current_cpt_revision_id'] is None:",
    "        cpt_hash = contracts.canonical_hash(list(baseline['validated_tables']))",
    "        resolved = baseline['id']",
    "    else:",
    "        assert review['cpt_hash'] is not None",
    "        cpt_hash = str(review['cpt_hash'])",
    "        assert review['calculation_result'] is not None",
    "        resolved = str(review['calculation_result']['id'])",
    "    return {'baseline_id': str(baseline['id']), 'current_cpt_revision_id': review['current_cpt_revision_id'], 'cpt_hash': str(cpt_hash), 'result_id': str(resolved), 'input_hash': str(review['input_freshness']['current_fingerprint']), 'expected_review_revision': int(review['review_revision'])}",
    "",
    "made = need(*call('POST', '/api/v1/patients', {'identifier': IDENTIFIER, 'given_name': 'ReportGiven', 'family_name': 'ReportFamily', 'sex': 'F', 'age': 30, 'clinical_status': 'first_time'}, PHEAD), 201, 'patient create')",
    "ENC = made['draft']['id']",
    "PATIENT = made['patient']['id']",
    "saved = need(*call('PATCH', '/api/v1/encounters/' + ENC, {'draft_data': {'history': {'values': {'a1': 'yes', 'b1': 'no', 'a2': 'yes', 'b2': 'no'}}, 'gate': 'true'}}, {**PHEAD, 'If-Match': ETAG(1)}), 200, 'history save')",
    "REV = int(saved['revision'])",
    "meds = need(*call('POST', '/api/v1/encounters/' + ENC + '/medications', {'medications': [{'catalog_drug_id': 'catalog_alpha'}]}, {**PHEAD, 'If-Match': ETAG(REV)}), 200, 'meds save')",
    "REV = int(meds['revision'])",
    "started = need(*call('POST', '/api/v1/encounters/' + ENC + '/generation-batches', {'packages': [package('s53_q1', 'a1', 'b1'), package('s53_q2', 'a2', 'b2')]}, {**PHEAD, 'If-Match': ETAG(REV)}), 202, 'batch start')",
    "BATCH = started['batch']",
    "RUNS = {r['question_key']: r for r in started['question_runs']}",
    "run_generation(BATCH['id'])",
    "adjusted = need(*call('POST', '/api/v1/question-runs/' + RUNS['s53_q2']['id'] + '/cpt-adjustments', {'node_id': 'A', 'parent_states': [], 'state': 'yes', 'target_percentage': '40', 'expected_review_revision': 1}, PHEAD), 200, 'adjust q2')",
    "assert adjusted['revision']['after_row']['percentages'] == ['60', '40'], 'redistribution: ' + str(adjusted)[:200]",
    "drain_until(RUNS['s53_q2']['id'])",
    "ACC = {}",
    "REVMAP = {}",
    "for key in ('s53_q1', 's53_q2'):",
    "    _status, review = call('GET', '/api/v1/question-runs/' + RUNS[key]['id'] + '/review')",
    "    assert _status == 200, 'review ' + key",
    "    posted = need(*call('POST', '/api/v1/question-runs/' + RUNS[key]['id'] + '/acceptance', accept_body(review), PHEAD), 200, 'accept ' + key)",
    "    ACC[RUNS[key]['id']] = posted['acceptance']",
    "    REVMAP[RUNS[key]['id']] = int(posted['review_revision'])",
    "draft = need(*call('GET', '/api/v1/encounters/' + ENC), 200, 'draft read')",
    "need(*call('POST', '/api/v1/encounters/' + ENC + '/notes', {'page': 'proposal', 'text': NOTE_EVIL}, {**PHEAD, 'If-Match': ETAG(draft['revision'])}), 201, 'note create')",
    "REV = int(need(*call('GET', '/api/v1/encounters/' + ENC), 200, 'draft reread')['revision'])",
    "need(*call('PATCH', '/api/v1/encounters/' + ENC + '/secondary-plan', {'text': PLAN_EVIL, 'expected_plan_revision': 1}, {**PHEAD, 'If-Match': ETAG(1)}), 200, 'plan save')",
    "sign_payload = {'expected_encounter_revision': REV, 'expected_plan_revision': 2, 'batch_id': BATCH['id'], 'acceptances': [{'question_run_id': rid, 'acceptance_id': acc['id'], 'expected_review_revision': REVMAP[rid]} for rid, acc in sorted(ACC.items())]}",
    "need(*call('POST', '/api/v1/encounters/' + ENC + '/sign', sign_payload, {**PHEAD, 'If-Match': ETAG(REV)}), 200, 'sign registration')",
    "freed = need(*call('POST', '/api/v1/patients/' + PATIENT + '/encounters', {'kind': 'follow_up'}, PHEAD), 201, 'followup create')",
    "FU = freed['encounter']['id']",
    "saved2 = need(*call('PATCH', '/api/v1/encounters/' + FU, {'draft_data': {'history': {'values': {'fa': 'yes', 'fb': 'no'}}, 'gate': 'true'}}, {**PHEAD, 'If-Match': ETAG(1)}), 200, 'fu history')",
    "REV2 = int(saved2['revision'])",
    "meds2 = need(*call('POST', '/api/v1/encounters/' + FU + '/medications', {'medications': [{'catalog_drug_id': 'catalog_alpha'}]}, {**PHEAD, 'If-Match': ETAG(REV2)}), 200, 'fu meds')",
    "REV2 = int(meds2['revision'])",
    "started2 = need(*call('POST', '/api/v1/encounters/' + FU + '/generation-batches', {'packages': [package('s53_f1', 'fa', 'fb')]}, {**PHEAD, 'If-Match': ETAG(REV2)}), 202, 'fu batch')",
    "BATCH2 = started2['batch']",
    "RUNS2 = {r['question_key']: r for r in started2['question_runs']}",
    "run_generation(BATCH2['id'])",
    "_status, review2 = call('GET', '/api/v1/question-runs/' + RUNS2['s53_f1']['id'] + '/review')",
    "assert _status == 200, 'fu review'",
    "accepted2 = need(*call('POST', '/api/v1/question-runs/' + RUNS2['s53_f1']['id'] + '/acceptance', accept_body(review2), PHEAD), 200, 'fu accept')",
    "need(*call('PATCH', '/api/v1/encounters/' + FU + '/secondary-plan', {'text': 'Follow-up: continue current care.', 'expected_plan_revision': 1}, {**PHEAD, 'If-Match': ETAG(1)}), 200, 'fu plan')",
    "sign2 = {'expected_encounter_revision': REV2, 'expected_plan_revision': 2, 'batch_id': BATCH2['id'], 'acceptances': [{'question_run_id': RUNS2['s53_f1']['id'], 'acceptance_id': accepted2['acceptance']['id'], 'expected_review_revision': int(accepted2['review_revision'])}]}",
    "signed2 = need(*call('POST', '/api/v1/encounters/' + FU + '/sign', sign2, {**PHEAD, 'If-Match': ETAG(REV2)}), 200, 'sign followup')",
    "SREV2 = int(signed2['revision'])",
    "other_login = need(*call('POST', '/api/v1/auth/login', {'username': OTHER, 'password': PASSWORD, 'role': 'physician'}), 200, 'other login')",
    "OHEAD = {'X-CSRF-Token': other_login['csrf_token']}",
    "need(*call('POST', '/api/v1/encounters/' + FU + '/addenda', {'text': ADDENDUM_EVIL, 'expected_encounter_revision': SREV2}, {**OHEAD, 'If-Match': ETAG(SREV2)}), 201, 'addendum')",
    "print(json.dumps({'username': PHYSICIAN, 'password': PASSWORD, 'otherUsername': OTHER, 'patientId': PATIENT, 'identifier': IDENTIFIER, 'encounterId': ENC, 'followupId': FU}))",
  ];
  // Multiprocessing spawn re-imports this script in inference children (as
  // __mp_main__): only defs may live at top level. Indent every executable
  // line after the marker under `if __name__ == ...` so children stay inert.
  const marker = scriptLines.indexOf("### RUN ###");
  const scriptHead = scriptLines.slice(0, marker);
  const scriptTail = scriptLines
    .slice(marker + 1)
    .map((line) => (line === "" ? line : `        ${line}`));
  return scriptHead
    .concat(["if __name__ == '__main__':", "    try:"])
    .concat(scriptTail)
    .concat([
      "    finally:",
      "        try:",
      "            restore_queue()",
      "            print('QUEUE-RESTORED')",
      "        except Exception as exc:",
      "            print('RESTORE-WARNING: ' + str(exc)[:200])",
    ])
    .join("\n");
}

let cachedFixture: Promise<SignedFixture> | null = null;

function runHelper(): Promise<SignedFixture> {
  const dbUrl = process.env.DATABASE_URL ?? "";
  expect(
    dbUrl,
    "DATABASE_URL must be set (source the repo .env) so the signing helper targets the dev database",
  ).toBeTruthy();
  expect(dbUrl, "helper must never touch the test database").not.toContain(
    "x_insight_test",
  );
  const username = uniqueName("e2es53rep");
  const other = uniqueName("e2es53add");
  const password = "secret123";
  const identifier = uniquePatientId();
  const scriptPath = join(
    tmpdir(),
    `s53e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}.py`,
  );
  writeFileSync(scriptPath, helperSource());
  return new Promise<SignedFixture>((resolvePromise, reject) => {
    execFile(
      VENV_PYTHON,
      [scriptPath, API_BASE, username, password, other, identifier, REPO_ROOT],
      { timeout: 540000, maxBuffer: 16 * 1024 * 1024, env: process.env },
      (error, stdout, stderr) => {
        if (error) {
          reject(
            new Error(
              `signed-fixture helper failed: ${error.message}\nSTDERR:\n${stderr}\nSTDOUT:\n${stdout}`,
            ),
          );
          return;
        }
        try {
          // The helper prints progress lines plus QUEUE-RESTORED; the
          // fixture record is the single {"username": ...} JSON line.
          const jsonLine = stdout
            .split("\n")
            .map((line) => line.trim())
            .find((line) => line.startsWith('{"username"'));
          if (!jsonLine) {
            throw new Error("no fixture JSON line in helper output");
          }
          resolvePromise(JSON.parse(jsonLine) as SignedFixture);
        } catch (parseError) {
          reject(
            new Error(
              `signed-fixture helper printed no JSON: ${parseError}\nSTDOUT:\n${stdout}\nSTDERR:\n${stderr}`,
            ),
          );
        }
      },
    );
  });
}

function signedFixture(): Promise<SignedFixture> {
  if (cachedFixture === null) {
    cachedFixture = runHelper();
  }
  return cachedFixture;
}

async function loginAdmin(page: import("@playwright/test").Page): Promise<void> {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible({ timeout: 15000 });
}

async function loginPhysician(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  prefix: string,
): Promise<{ username: string; password: string }> {
  const username = uniqueName(prefix);
  const password = "secret123";
  await ensurePhysician(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  return { username, password };
}

async function createPatientViaApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ patientId: string; identifier: string }> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
  };
  const post = (csrfToken: string) =>
    request.post("/api/v1/patients", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
      data: payload,
    });
  let create = await post(await physicianSession(request, physician));
  if (create.status() === 401) {
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    patientId: body.patient.id as string,
    identifier: payload.identifier,
  };
}

// S53 §§1-2: admin login → Exports nav → both real CSV downloads.
test("S53 §§1-2 admin exports page downloads both lists", async ({ page }) => {
  await loginAdmin(page);
  await expect(page.getByRole("link", { name: "Exports" })).toBeVisible();
  await page.getByRole("link", { name: "Exports" }).click();
  await expect(page.getByTestId("exports-heading")).toBeVisible({ timeout: 15000 });

  const patients = page.waitForEvent("download");
  await page.getByTestId("exports-patients").click();
  const patientsDownload = await patients;
  expect(patientsDownload.suggestedFilename()).toBe("patients.csv");
  await expect(page.getByTestId("exports-status")).toContainText(
    /Downloaded patients\.csv \(\d+ bytes\)\./,
    { timeout: 15000 },
  );

  const physicians = page.waitForEvent("download");
  await page.getByTestId("exports-physicians").click();
  const physiciansDownload = await physicians;
  expect(physiciansDownload.suggestedFilename()).toBe("physicians.csv");
  await expect(page.getByTestId("exports-status")).toContainText(
    /Downloaded physicians\.csv \(\d+ bytes\)\./,
    { timeout: 15000 },
  );
});

// S53 §3: real signed multi-encounter report in the browser — research
// label, chronology, per-question CPTs/adjustments, addenda, inert markup.
test("S53 §3 signed patient report renders chronology, CPTs, adjustments, addenda", async ({
  page,
}) => {
  test.setTimeout(600000);
  const fx = await signedFixture();
  let dialogSeen: string | null = null;
  page.on("dialog", (dialog) => {
    dialogSeen = `${dialog.type()}: ${dialog.message()}`;
    void dialog.dismiss().catch(() => undefined);
  });

  await loginAdmin(page);
  await page.goto(`/#/patients/${fx.patientId}/report`);
  await expect(page.getByTestId("report-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("report-research-label")).toContainText(
    "research prototype",
  );
  await expect(page.getByTestId("report-status")).toContainText(
    "Signed-only printable report loaded.",
    { timeout: 15000 },
  );
  const frame = page.frameLocator("#report-frame");
  const bodyText = await frame.locator("body").textContent({ timeout: 15000 });
  const text = bodyText ?? "";
  // Chronology across both signed encounters.
  expect(text).toContain("Signed chronology");
  expect(text).toContain(`Signed encounter ${fx.encounterId}`);
  expect(text).toContain(`Signed encounter ${fx.followupId}`);
  // Per-question original vs accepted content and indicators.
  for (const key of ["s53_q1", "s53_q2", "s53_f1"]) {
    expect(text, key).toContain(key);
  }
  expect(text).toContain("Unchanged original");
  expect(text).toContain("Physician-adjusted");
  expect(text).toContain("Final equals original");
  // Worked CPT literals from the real pipeline (original 80/20, adjusted 60/40).
  expect(text).toContain("80");
  expect(text).toContain("60");
  // Attribution: accepting/signing physician, addendum author, frozen note.
  expect(text).toContain(fx.username);
  expect(text).toContain(fx.otherUsername);
  expect(text).toContain("Addenda");
  expect(text).toContain("Keep monitoring monthly.");
  expect(text).toContain("Proposal looks complete.");
  expect(text).toContain("Correction");
  // Malicious markup renders as inert text (decoded entities in textContent).
  expect(text).toContain(EVIL_PLAN);
  expect(text).toContain(EVIL_NOTE);
  expect(text).toContain(EVIL_ADDENDUM);
  // No script from the report ever executed.
  expect(dialogSeen).toBeNull();
});

// S53 §4: print preview — label + frame stay readable, chrome hides, the
// served document carries its own print page-break rules.
test("S53 §4 report print preview keeps the label readable with page breaks", async ({
  page,
  request,
}) => {
  test.setTimeout(600000);
  const fx = await signedFixture();
  await loginAdmin(page);
  await page.goto(`/#/patients/${fx.patientId}/report`);
  await expect(page.getByTestId("report-frame")).toBeVisible({ timeout: 15000 });

  await page.emulateMedia({ media: "print" });
  // Research label and frame print; nav, actions, and buttons hide.
  await expect(page.getByTestId("report-research-label")).toBeVisible();
  await expect(page.getByTestId("report-frame")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
  await expect(page.getByTestId("report-print")).toBeHidden();
  await page.emulateMedia({ media: "screen" });

  // The served document itself carries print rules + the research label.
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok()).toBeTruthy();
  const report = await request.get(`/api/v1/patients/${fx.patientId}/report`, {
    headers: { Accept: "text/html" },
  });
  expect(report.status()).toBe(200);
  const html = await report.text();
  expect(html).toContain("@media print");
  expect(html).toContain("page-break-before:always");
  expect(html).toContain("Research prototype");
});

// S53 §3: unsigned patient → honest empty state rendered inside the signed-only
// frame (the backend always serves the full escaped document; the frame body
// carries "No signed encounters." — the ReportPage empty-string branch only
// fires when the endpoint returns literally "", which never happens).
test("S53 §3 unsigned patient report shows the honest empty state", async ({
  page,
  request,
}) => {
  const physician = {
    username: uniqueName("e2es53unsigned"),
    password: "secret123",
  };
  await ensurePhysician(request, physician.username, physician.password);
  const { patientId } = await createPatientViaApi(request, physician);
  await loginAdmin(page);
  await page.goto(`/#/patients/${patientId}/report`);
  await expect(page.getByTestId("report-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("report-research-label")).toContainText(
    "research prototype",
  );
  const frame = page.frameLocator("#report-frame");
  await expect(frame.getByText("No signed encounters.")).toBeVisible({
    timeout: 15000,
  });
});

// S53 §§1+3: physician has no Exports nav, no chart report link, and both
// direct routes render the guard instead of content.
test("S53 §§1+3 physician sees no exports nav or report link and both routes guard", async ({
  page,
  request,
}) => {
  test.setTimeout(600000);
  const fx = await signedFixture();
  await loginPhysician(page, request, "e2es53phys");
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Exports" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();

  await page.goto("/#/exports");
  await expect(page.getByText("Administrator access required.")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("exports-heading")).toHaveCount(0);

  await page.goto(`/#/patients/${fx.patientId}/report`);
  await expect(page.getByText("Administrator access required.")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("report-frame")).toHaveCount(0);

  // The physician chart offers no printable-report link (admin-only).
  await page.goto(`/#/patients/${fx.patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("chart-report-link")).toHaveCount(0);

  // The admin chart does offer the printable-report link.
  await signOut(page);
  await loginAdmin(page);
  await page.goto(`/#/patients/${fx.patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("chart-report-link")).toBeVisible();
});

// S53 §4 (cheap): dark theme renders exports + report; keyboard drives a
// download and walks the report actions without touching Print.
test("S53 §4 exports and report work in dark theme and via keyboard", async ({
  page,
}) => {
  test.setTimeout(600000);
  const fx = await signedFixture();
  await loginAdmin(page);
  const themeOf = (): Promise<string | null> =>
    page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  if ((await themeOf()) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(themeOf).toBe("dark");

  await page.getByRole("link", { name: "Exports" }).click();
  await expect(page.getByTestId("exports-heading")).toBeVisible({ timeout: 15000 });
  // Keyboard-only download: focus + Enter triggers the real attachment.
  const download = page.waitForEvent("download");
  await page.getByTestId("exports-patients").focus();
  await expect(page.getByTestId("exports-patients")).toBeFocused();
  await page.keyboard.press("Enter");
  const attachment = await download;
  expect(attachment.suggestedFilename()).toBe("patients.csv");
  await expect(page.getByTestId("exports-status")).toContainText(
    /Downloaded patients\.csv \(\d+ bytes\)\./,
    { timeout: 15000 },
  );

  await page.goto(`/#/patients/${fx.patientId}/report`);
  await expect(page.getByTestId("report-frame")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("report-research-label")).toBeVisible();
  // Keyboard-only walk of the report actions: Print is focusable, and Enter
  // on Back to chart navigates without invoking window.print().
  await page.getByTestId("report-print").focus();
  await expect(page.getByTestId("report-print")).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });

  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect.poll(themeOf).toBe("light");
});
