import { test } from 'node:test';
import assert from 'node:assert/strict';
import { en, fa, message, present, presentError, researchWarningPresentation, researchWarningSource, missingMessages, clinicalText, missingClinicalMessages } from '../src/shared/messages.ts';
import { readFileSync } from 'node:fs';

test('retained compound failures and numeric proposal pauses translate without altering diagnostics', () => {
  const diagnostic = 'Raw external diagnostic English/فارسی';
  const failed = present('fa', `Failed after 2 of 3 attempts. Last error: ${diagnostic}.`);
  assert.ok(failed.includes(diagnostic));
  assert.ok(!failed.includes('Last error'));
  assert.ok(!failed.includes('attempts'));
  for (const [count, suffix, verb] of [[1, '', 's'], [2, 's', '']]) {
    const detail = `${count} proposal input${suffix} need${verb} clarification before generation. Uncertain context below does not block generation.`;
    assert.ok(!present('fa', detail).includes('clarification before'));
    assert.ok(!present('fa', `Generation paused. ${detail} Resolve the blocking inputs below before starting a new run.`).includes('clarification before'));
  }
  assert.equal(present('fa', diagnostic), diagnostic);
});

test('both catalogs cover every key with the same named substitutions', () => {
  assert.deepEqual(Object.keys(fa).sort(), Object.keys(en).sort());
  assert.deepEqual(missingMessages(en, fa), []);
  assert.deepEqual(missingMessages(en, { ...fa, signIn: '' }), ['signIn']);
  for (const key of Object.keys(en)) {
    const parameters = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
    assert.deepEqual(parameters(fa[key]), parameters(en[key]), key);
  }
  assert.equal(message('fa', 'signedInAs', { username: 'Ali' }), 'واردشده با نام کاربری Ali');
});

test('warning presentation requires both the exact source and version', () => {
  const translated = researchWarningPresentation('fa', 'research-warning-v2', researchWarningSource);
  assert.equal(translated.available, true);
  assert.ok(translated.text.includes('از نظر بالینی اعتبارسنجی نشده'));
  for (const [version, source] of [['research-warning-v3', researchWarningSource], ['research-warning-v2', `${researchWarningSource} Changed.`]]) {
    const result = researchWarningPresentation('fa', version, source);
    assert.equal(result.available, false);
    assert.equal(result.text, source);
  }
  assert.equal(researchWarningPresentation('en', 'future', 'New complete wording.').text, 'New complete wording.');
});

test('retained canonical errors retranslate without altering their diagnostics', () => {
  const original = new Error('Invalid username, password, or role.');
  assert.equal(presentError('en', original.message), original.message);
  assert.equal(presentError('fa', original.message), 'نام کاربری، گذرواژه یا نقش نامعتبر است.');
  assert.equal(original.message, 'Invalid username, password, or role.');
  assert.ok(presentError('fa', 'Unrecognized diagnostic 123').endsWith('Unrecognized diagnostic 123'));
});

test('proposal, signed records, recovery and reporting chrome have Persian presentations', () => {
  for (const source of ['No CPT tables recorded.', 'No acceptance recorded.', 'Commit staged restore', 'Print report', 'Loading proposal readiness…']) {
    assert.notEqual(present('fa', source), source, source);
    assert.equal(present('en', source), source);
  }
  assert.equal(present('fa', 'A physician wrote this: No CPT tables recorded.'), 'A physician wrote this: No CPT tables recorded.');
});

test('active assessment prompts are covered by source and version, not just item ID', () => {
  const definitions = ['diagnosis.v1', 'panss.v1', 'cssrs.v2'].map((name) => JSON.parse(readFileSync(new URL(`../../content/assessments/${name}.json`, import.meta.url))));
  assert.deepEqual(missingClinicalMessages(definitions), []);
  const diagnosis = definitions[0];
  const item = diagnosis.items[0];
  assert.ok(clinicalText('fa', diagnosis.id, diagnosis.version, item.id, item.prompt).startsWith('هذیان‌ها:'));
  assert.ok(clinicalText('fa', diagnosis.id, 'future', item.id, item.prompt).includes(item.prompt));
  assert.ok(clinicalText('fa', diagnosis.id, diagnosis.version, item.id, 'Changed source').includes('Changed source'));
  assert.deepEqual(missingClinicalMessages([{ ...diagnosis, items: [...diagnosis.items, { id: 'new', prompt: 'New item' }] }]), ['diagnosis/v1/new']);
});

test('untranslated workflow definitions retain the original with an explicit Persian fallback', () => {
  const source = 'Original workflow definition';
  assert.equal(clinicalText('en', 'workflow', 'candidate/history/example.v1/field', 'definition', source), source);
  const fallback = clinicalText('fa', 'workflow', 'candidate/history/example.v1/field', 'definition', source);
  assert.ok(fallback.includes(message('fa', 'translationUnavailable')));
  assert.ok(fallback.endsWith(source));
});

test('DDI presentation keeps source values intact in localized templates', () => {
  assert.equal(message('en', 'ddiSeverityDirection', { severity: 'major', direction: 'A -> B' }), 'Severity: major · direction: A -> B');
  const translated = message('fa', 'ddiSeverityDirection', { severity: 'major', direction: 'A -> B' });
  assert.ok(translated.includes('major'));
  assert.ok(translated.includes('A -> B'));
});

const workflowDefinitionNames = [
  'pharmacotherapy.v3', 'high_suicide_clozapine.v2', 'lai_indication_choice.v2',
  'aggression_clozapine.v2', 'established_case_clozapine.v1', 'tardive_dyskinesia.v1',
  'akathisia.v1', 'parkinsonism.v1', 'acute_dystonia.v1',
  'no_improvement_clozapine.v1', 'continue_or_adjust.v1',
];

test('every active workflow definition and assessment guidance has source-bound Persian wording', () => {
  let count = 0;
  for (const name of workflowDefinitionNames) {
    const definition = JSON.parse(readFileSync(new URL(`../../content/history/history.${name}.json`, import.meta.url)));
    for (const field of definition.fields) {
      const items = ['definition', 'operationalization'].filter((key) => field[key]).map((key) => ({ id: key, prompt: field[key] }));
      const presentation = { id: 'workflow', version: field.source_path, items };
      assert.deepEqual(missingClinicalMessages([presentation]), [], name);
      for (const item of items) {
        count += 1;
        assert.notEqual(clinicalText('fa', 'workflow', field.source_path, item.id, item.prompt), item.prompt);
        assert.equal(clinicalText('en', 'workflow', field.source_path, item.id, item.prompt), item.prompt);
        assert.ok(clinicalText('fa', 'workflow', field.source_path, item.id, `${item.prompt} Changed.`).endsWith('Changed.'));
        assert.ok(clinicalText('fa', 'workflow', `${field.source_path}/future`, item.id, item.prompt).endsWith(item.prompt));
      }
      assert.deepEqual(missingClinicalMessages([{ ...presentation, items: [...items, { id: 'new', prompt: 'New required guidance' }] }]), [`workflow/${field.source_path}/new`]);
    }
  }
  assert.equal(count, 208);
});

test('workflow response labels localize independently of their persisted category and drug names', () => {
  const drugs = new Set(['Aripiprazole', 'Asenapine', 'Brexpiprazole', 'Cariprazine', 'Chlorpromazine', 'Clozapine', 'Fluphenazine', 'Haloperidol', 'Iloperidone', 'Loxapine', 'Lurasidone', 'Molindone', 'Olanzapine', 'Paliperidone', 'Perphenazine', 'Pimozide', 'Quetiapine', 'Risperidone', 'Thioridazine', 'Thiothixene', 'Trifluoperazine', 'Ziprasidone']);
  for (const name of workflowDefinitionNames) {
    const definition = JSON.parse(readFileSync(new URL(`../../content/history/history.${name}.json`, import.meta.url)));
    for (const field of definition.fields) {
      for (const value of field.permitted) {
        const label = value.replace(/([a-z])([A-Z])/g, '$1 $2');
        if (drugs.has(value)) assert.equal(present('fa', label), label);
        else assert.notEqual(present('fa', label), label, `${name}: ${value}`);
      }
      assert.notEqual(present('fa', field.time_window.replaceAll('_', ' ')), field.time_window.replaceAll('_', ' '));
    }
  }
});

test('retained async sentences translate without changing their interpolated identifiers', () => {
  assert.equal(present('en', 'Saved (revision 123)'), 'Saved (revision 123)');
  assert.equal(present('fa', 'Saved (revision 123)'), 'ذخیره شد (نسخهٔ 123)');
  assert.equal(present('fa', 'Backup ready (00123 bytes).'), 'پشتیبان آماده است (00123 بایت).');
  assert.equal(message('fa', 'addValue1', { value1: 'Clozapine نام mixed' }), 'افزودن Clozapine نام mixed');
  assert.equal(present('fa', 'Patient-owned prose: Saved (revision 123)'), 'Patient-owned prose: Saved (revision 123)');
});

test("new-case server error and section keys have both language entries", () => {
  for (const key of ["new_case.errors.diagnosis_not_positive", "new_case.errors.bmi_category_required", "new_case.sections.care_setting", "new_case.sections.antipsychotic_candidates"]) {
    assert.ok(en[key]?.trim(), key);
    assert.ok(fa[key]?.trim(), key);
    assert.notEqual(en[key], fa[key], key);
  }
});
