import { test } from 'node:test';
import assert from 'node:assert/strict';
import { en, fa, message, present, presentError, researchWarningPresentation, researchWarningSource, missingMessages, clinicalText, missingClinicalMessages } from '../src/shared/messages.ts';
import { readFileSync } from 'node:fs';

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
  const translated = researchWarningPresentation('fa', 'research-warning-v1', researchWarningSource);
  assert.equal(translated.available, true);
  assert.ok(translated.text.includes('از نظر بالینی اعتبارسنجی نشده'));
  for (const [version, source] of [['research-warning-v2', researchWarningSource], ['research-warning-v1', `${researchWarningSource} Changed.`]]) {
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
