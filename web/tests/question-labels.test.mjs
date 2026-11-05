import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { questionLabels, questionLabel, missingQuestionLabels } from '../src/features/reasoning/questionLabels.ts';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
test('all active workflow questions have explicit labels', () => {
  const release = read('../../content/bundles/active-release.json');
  const keys = Object.values(release.bundles).flatMap(({ path }) => read(`../../${path}`).questions.map(({ question_key }) => question_key));
  assert.deepEqual(missingQuestionLabels(keys), []);
  assert.deepEqual(missingQuestionLabels([...keys, 'unmapped_question']), ['unmapped_question']);
  assert.equal(questionLabels.pharmacotherapy, 'Pharmacotherapy');
});
test('unknown questions have a readable fallback and development warning without the key', () => {
  const warnings = [];
  assert.equal(questionLabel('NEW__Question-key', true, (message) => warnings.push(message)), 'New question key');
  assert.equal(warnings.length, 1);
  assert.ok(!warnings[0].includes('NEW__Question-key'));
  questionLabel('new_key', false, (message) => warnings.push(message));
  assert.equal(warnings.length, 1);
});

test('active questions have Persian labels and unknown questions retain readable source', () => {
  assert.equal(questionLabel('pharmacotherapy', false, console.warn, 'fa'), 'دارودرمانی');
  for (const key of Object.keys(questionLabels)) {
    assert.notEqual(questionLabel(key, false, console.warn, 'fa'), questionLabels[key]);
  }
  assert.equal(questionLabel('NEW__Question-key', false, console.warn, 'fa'), 'ترجمه در دسترس نیست؛ متن اصلی در ادامه آمده است. New question key');
});
