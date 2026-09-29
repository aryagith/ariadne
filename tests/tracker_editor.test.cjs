// Run with: node --test tests/tracker_editor.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = vm.createContext({
  afterApply: ['APPLIED', 'OA', 'INTERVIEW', 'OFFER', 'REJECTED', 'WITHDRAWN'],
  document: {
    getElementById: () => ({addEventListener() {}}),
    querySelectorAll: () => [],
    querySelector: () => ({addEventListener() {}}),
  },
});
vm.runInContext(fs.readFileSync('backend/tracker_editor.js', 'utf8'), context);
const original = 'Status: DISCOVERED\nPosted: UNKNOWN\nPosted source: UNKNOWN\nNotes:\nPreserve my notes.\n';

test('status and date save preserves notes; invalid dates and unsafe resets fail', () => {
  const result = context.updatedStatus(original, 'SHORTLISTED', '2026-09-28', 'Employer date field', 'DISCOVERED');
  assert.equal(result, 'Status: SHORTLISTED\nPosted: 2026-09-28\nPosted source: Employer date field\nNotes:\nPreserve my notes.\n');
  assert.throws(() => context.updatedStatus(original, 'SHORTLISTED', '2026-02-30', 'Employer', 'DISCOVERED'), /valid/);
  assert.throws(() => context.updatedStatus(original, 'SHORTLISTED', '2026-09-28', 'UNKNOWN', 'DISCOVERED'), /evidence/);
  assert.throws(() => context.updatedStatus(original, 'SHORTLISTED', '2026-09-28T12:00:00', 'Employer', 'DISCOVERED'), /timezone/);
  assert.throws(() => context.updatedStatus(original, 'DISCOVERED', 'UNKNOWN', 'UNKNOWN', 'APPLIED'), /reset/);
  assert.throws(() => context.updatedStatus(original.replace('DISCOVERED', 'SUBMISSION_UNKNOWN'), 'DISCOVERED', 'UNKNOWN', 'UNKNOWN', 'DISCOVERED'), /reset/);
});

test('file save commits exact text; conflicting edits and denied writes are preserved', async () => {
  let contents = original, opened = 0;
  const handle = {
    getFile: async () => ({text: async () => contents}),
    createWritable: async () => {
      opened++;
      let pending;
      return {write: async text => {pending = text;}, close: async () => {contents = pending;}, abort: async () => {}};
    },
  };
  const notes = 'Follow up next week.\nAsk about the Toronto team. <draft>\n';
  const updated = context.updatedStatus(original, 'APPLIED', 'UNKNOWN', 'UNKNOWN', 'DISCOVERED', notes);
  await context.saveStatus(handle, original, updated);
  assert.equal(contents, updated);
  assert.equal(context.parseStatus(contents).notes, notes);
  assert.equal(context.parseStatus(context.updatedStatus(original, 'DISCOVERED', 'UNKNOWN', 'UNKNOWN', 'DISCOVERED', '')).notes, '');
  assert.throws(() => context.updatedStatus(original, 'DISCOVERED', 'UNKNOWN', 'UNKNOWN', 'DISCOVERED', 'x'.repeat(20001)), /20,000/);
  await assert.rejects(context.saveStatus(handle, original, 'stale overwrite'), /changed/);
  assert.equal(opened, 1);
  handle.createWritable = async () => {throw Error('Permission denied');};
  await assert.rejects(context.saveStatus(handle, updated, 'denied overwrite'), /Permission denied/);
  assert.equal(contents, updated);
});
