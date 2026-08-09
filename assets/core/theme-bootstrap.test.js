const test = require('node:test');
const assert = require('node:assert/strict');

const theme = require('./theme-bootstrap.js');

const at = (hour, minute = 0) => new Date(2026, 8, 30, hour, minute, 0, 0);

test('normalizes legacy and unknown preferences', () => {
  assert.equal(theme.normalizePreference('system'), 'auto');
  assert.equal(theme.normalizePreference('light'), 'morning');
  assert.equal(theme.normalizePreference('dark'), 'evening');
  assert.equal(theme.normalizePreference('unknown'), 'auto');
});

test('resolves every automatic boundary without overlap', () => {
  assert.equal(theme.resolveAutomaticTheme(at(5, 59), false), 'morning');
  assert.equal(theme.resolveAutomaticTheme(at(5, 59), true), 'evening');
  assert.equal(theme.resolveAutomaticTheme(at(6)), 'morning');
  assert.equal(theme.resolveAutomaticTheme(at(9, 59)), 'morning');
  assert.equal(theme.resolveAutomaticTheme(at(10)), 'midday');
  assert.equal(theme.resolveAutomaticTheme(at(13, 59)), 'midday');
  assert.equal(theme.resolveAutomaticTheme(at(14)), 'afternoon');
  assert.equal(theme.resolveAutomaticTheme(at(17, 59)), 'afternoon');
  assert.equal(theme.resolveAutomaticTheme(at(18)), 'evening');
  assert.equal(theme.resolveAutomaticTheme(at(20, 59)), 'evening');
  assert.equal(theme.resolveAutomaticTheme(at(21), false), 'morning');
  assert.equal(theme.resolveAutomaticTheme(at(21), true), 'evening');
});

test('schedules the next change boundary', () => {
  assert.equal(theme.millisecondsUntilNextBoundary(at(5, 59)), 60_050);
  assert.equal(theme.millisecondsUntilNextBoundary(at(20, 59)), 60_050);
  assert.equal(theme.millisecondsUntilNextBoundary(at(21)), 9 * 60 * 60 * 1000 + 50);
});
