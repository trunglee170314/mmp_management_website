(() => {
  const legacyPreferences = Object.freeze({
    system: 'auto',
    light: 'morning',
    dark: 'evening',
  });
  const validPreferences = new Set(['auto', 'morning', 'midday', 'afternoon', 'evening']);
  const themeColors = Object.freeze({
    morning: '#f5f7f2',
    midday: '#f4f8f4',
    afternoon: '#f7f1e7',
    evening: '#0f1726',
  });
  const boundaryHours = Object.freeze([6, 10, 14, 18, 21]);

  const normalizePreference = preference => {
    const normalized = legacyPreferences[preference] || preference;
    return validPreferences.has(normalized) ? normalized : 'auto';
  };

  const resolveAutomaticTheme = (date = new Date(), prefersDark = false) => {
    const hour = date.getHours();
    if (hour >= 6 && hour < 10) return 'morning';
    if (hour >= 10 && hour < 14) return 'midday';
    if (hour >= 14 && hour < 18) return 'afternoon';
    if (hour >= 18 && hour < 21) return 'evening';
    return prefersDark ? 'evening' : 'morning';
  };

  const resolveTheme = (preference, date = new Date(), prefersDark = false) => (
    normalizePreference(preference) === 'auto'
      ? resolveAutomaticTheme(date, prefersDark)
      : normalizePreference(preference)
  );

  const millisecondsUntilNextBoundary = (date = new Date()) => {
    const nextBoundary = boundaryHours
      .map(hour => new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour))
      .find(boundary => boundary > date)
      || new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1, boundaryHours[0]);
    return Math.max(1000, nextBoundary.getTime() - date.getTime() + 50);
  };

  const api = Object.freeze({
    boundaryHours,
    millisecondsUntilNextBoundary,
    normalizePreference,
    resolveAutomaticTheme,
    resolveTheme,
    themeColors,
  });

  if (typeof window !== 'undefined') window.MMPTheme = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
