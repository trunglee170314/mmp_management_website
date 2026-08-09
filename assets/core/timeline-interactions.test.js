const test = require('node:test');
const assert = require('node:assert/strict');
const timeline = require('./timeline-interactions.js');

test('moving a task preserves its inclusive length across month/year and DST boundaries', () => {
  assert.deepEqual(timeline.range('2026-12-30', '2027-01-02', 3, 'move'), {start: '2027-01-02', due: '2027-01-05'});
  assert.equal(timeline.shift('2026-03-08', 1), '2026-03-09');
  assert.equal(timeline.shift('2024-02-28', 1), '2024-02-29');
});
test('resize clamps at the opposite end rather than reversing dates', () => {
  assert.deepEqual(timeline.range('2026-11-01', '2026-11-03', 20, 'start'), {start: '2026-11-03', due: '2026-11-03'});
  assert.deepEqual(timeline.range('2026-11-01', '2026-11-03', -20, 'due'), {start: '2026-11-01', due: '2026-11-01'});
});
test('single-date tasks acquire a coherent interval on drag', () => {
  assert.deepEqual(timeline.range('', '2026-11-03', 1, 'move'), {start: '2026-11-04', due: '2026-11-04'});
  assert.throws(() => timeline.range('', '', 1, 'move'));
});
test('dragging a range backwards sorts the selected dates', () => {
  assert.deepEqual(timeline.selectedRange('2026-11-01', 7, 2), {start: '2026-11-03', due: '2026-11-08'});
});
test('relative milestone labels distinguish past from completion and validate dates', () => {
  assert.equal(timeline.relative('2026-11-11', '2026-11-11'), 'Today');
  assert.equal(timeline.relative('2026-11-12', '2026-11-11'), 'In 1 day');
  assert.equal(timeline.relative('2026-11-09', '2026-11-11'), '2 days ago');
  assert.throws(() => timeline.ordinal('2026-02-30'));
});

test('palette state selects Auto or a preset case-insensitively without altering legacy colors', () => {
  const choices = ['', '#3B82F6', '#22C55E'];
  assert.deepEqual(timeline.paletteState('', choices).map(item => item.selected), [true, false, false]);
  assert.deepEqual(timeline.paletteState('#3b82f6', choices).map(item => item.selected), [false, true, false]);
  assert.equal(timeline.paletteState('#AABBCC', choices).some(item => item.selected), false);
});
test('read-only milestone palettes disable all color actions', () => {
  const state = timeline.paletteState('#3B82F6', ['', '#3B82F6'], false);
  assert.equal(state.every(item => item.disabled), true);
  assert.equal(state[1].selected, true);
});

test('calendar extremes reject overflow without emitting five-digit years', () => {
  assert.equal(timeline.shift('0001-01-01', 0), '0001-01-01');
  assert.equal(timeline.shift('9999-12-31', 0), '9999-12-31');
  assert.throws(() => timeline.shift('0001-01-01', -1));
  assert.throws(() => timeline.shift('9999-12-31', 1));
});
