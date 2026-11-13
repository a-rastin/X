import {en, fa} from "../src/shared/messages.ts";
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {followUpMessage} from '../src/features/follow-up/labels.ts';

test('follow-up history translates structured and humanized labels while retaining readable unknowns', () => {
  assert.equal(followUpMessage('fa', 'heart failure', fa), followUpMessage('fa', 'heart_failure', fa));
  assert.notEqual(followUpMessage('fa', 'panss_total', fa), 'panss total');
  assert.equal(followUpMessage('en', 'unknown_future_field', en), 'unknown future field');
  assert.equal(followUpMessage('fa', 'Olanzapine', fa), 'Olanzapine');
  assert.equal(followUpMessage('en', '2026-11-13T10:00:00Z', en), '2026-11-13T10:00:00Z');
});
