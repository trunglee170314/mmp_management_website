/* Pure UTC date arithmetic shared by pointer interactions and regression tests. */
((root, factory) => {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MMPTimeline = api;
})(typeof globalThis === 'object' ? globalThis : this, () => {
  const DAY = 86400000;
  const ordinal = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw new Error('Invalid date.');
    const result = Date.parse(`${value}T00:00:00Z`);
    if (!Number.isFinite(result) || new Date(result).toISOString().slice(0, 10) !== value) throw new Error('Invalid date.');
    return result / DAY;
  };
  const date = day => {
    if (!Number.isInteger(day) || day < ordinal('0001-01-01') || day > ordinal('9999-12-31')) throw new Error('Date is outside the supported calendar.');
    return new Date(day * DAY).toISOString().slice(0, 10);
  };
  const shift = (value, days) => date(ordinal(value) + days);
  const range = (start, due, days, mode) => {
    const first = start || due;
    const last = due || start;
    if (!first || !last) throw new Error('Schedule this task before dragging it.');
    if (mode === 'start') return {start: date(Math.min(ordinal(first) + days, ordinal(last))), due: last};
    if (mode === 'due') return {start: first, due: date(Math.max(ordinal(last) + days, ordinal(first)))};
    return {start: shift(first, days), due: shift(last, days)};
  };
  const selectedRange = (origin, firstDay, lastDay) => ({
    start: shift(origin, Math.min(firstDay, lastDay)),
    due: shift(origin, Math.max(firstDay, lastDay)),
  });
  const relative = (value, today) => {
    const days = ordinal(value) - ordinal(today);
    return days === 0 ? 'Today' : days > 0 ? `In ${days} day${days === 1 ? '' : 's'}` : `${Math.abs(days)} day${days === -1 ? '' : 's'} ago`;
  };
  const paletteState = (value, colors, canEdit = true) => colors.map(color => ({
    color,
    selected: String(color || '').toUpperCase() === String(value || '').toUpperCase(),
    disabled: !canEdit,
  }));
  return {ordinal, date, shift, range, selectedRange, relative, paletteState};
});
