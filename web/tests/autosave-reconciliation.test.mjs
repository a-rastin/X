import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reconcileAutosave} from '../src/features/encounters/reconcileAutosave.ts';

test('server attribution and canonical decimals survive a save without losing in-flight edits', () => {
  const sent = {answer: {manual: false, actor: null}, weight: '70.0', note: 'before'};
  const saved = {answer: {manual: false, actor: 'server'}, weight: '70', note: 'before'};
  assert.deepEqual(reconcileAutosave(sent, sent, saved), saved);
  const edited = {...sent, answer: {...sent.answer, manual: true}, note: 'typed during save'};
  assert.deepEqual(reconcileAutosave(sent, edited, saved), {
    answer: {manual: true, actor: 'server'}, weight: '70', note: 'typed during save'
  });
  assert.deepEqual(reconcileAutosave({entries: ['a']}, {entries: []}, {entries: ['a']}), {entries: []});
});
