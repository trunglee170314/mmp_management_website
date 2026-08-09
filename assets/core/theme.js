(() => {
  const root = document.documentElement;
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const themeSystem = window.MMPTheme;
  let saveRevision = 0;
  let autoThemeTimer = null;

  const resolveTheme = preference => (
    themeSystem.resolveTheme(preference, new Date(), systemTheme.matches)
  );

  const updateThemeColor = theme => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = themeSystem.themeColors[theme] || themeSystem.themeColors.morning;
  };

  const syncPicker = preference => {
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      const selected = button.dataset.themeChoice === preference;
      button.setAttribute('aria-checked', selected ? 'true' : 'false');
      button.tabIndex = selected ? 0 : -1;
      button.classList.toggle('is-selected', selected);
    });
  };

  const scheduleAutomaticTheme = () => {
    window.clearTimeout(autoThemeTimer);
    autoThemeTimer = window.setTimeout(() => {
      if (root.dataset.themePreference === 'auto') applyPreference('auto');
    }, themeSystem.millisecondsUntilNextBoundary());
  };

  const applyPreference = rawPreference => {
    const preference = themeSystem.normalizePreference(rawPreference);
    const theme = resolveTheme(preference);
    root.dataset.themePreference = preference;
    root.dataset.theme = theme;
    root.style.colorScheme = theme === 'evening' ? 'dark' : 'light';
    updateThemeColor(theme);
    syncPicker(preference);
    window.clearTimeout(autoThemeTimer);
    if (preference === 'auto') scheduleAutomaticTheme();
  };

  const csrfToken = picker => (
    picker.closest('.account-menu')?.querySelector('[name="csrfmiddlewaretoken"]')?.value || ''
  );

  const savePreference = async (picker, preference, previousPreference) => {
    const revision = ++saveRevision;
    const status = picker.querySelector('.appearance-status');
    const choices = [...picker.querySelectorAll('[data-theme-choice]')];
    choices.forEach(button => { button.disabled = true; });
    status.textContent = 'Saving…';
    const body = new FormData();
    body.append('theme', preference);
    try {
      const response = await fetch(picker.dataset.saveUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken(picker),
          'Accept': 'application/json',
        },
        body,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to save appearance.');
      if (revision !== saveRevision) return;
      applyPreference(data.theme);
      status.textContent = 'Saved';
      window.setTimeout(() => {
        if (status.textContent === 'Saved') status.textContent = '';
      }, 1600);
    } catch (error) {
      if (revision !== saveRevision) return;
      applyPreference(previousPreference);
      status.textContent = error.message || 'Not saved';
    } finally {
      if (revision === saveRevision) {
        choices.forEach(button => { button.disabled = false; });
      }
    }
  };

  applyPreference(root.dataset.themePreference || 'auto');

  document.addEventListener('click', event => {
    const choice = event.target.closest('[data-theme-choice]');
    if (!choice) return;
    const picker = choice.closest('[data-theme-picker]');
    const preference = themeSystem.normalizePreference(choice.dataset.themeChoice);
    const previousPreference = themeSystem.normalizePreference(root.dataset.themePreference);
    if (!picker || preference === previousPreference) return;
    applyPreference(preference);
    savePreference(picker, preference, previousPreference);
  });

  document.addEventListener('keydown', event => {
    const choice = event.target.closest('[data-theme-choice]');
    if (!choice) return;
    const picker = choice.closest('[data-theme-picker]');
    const choices = picker ? [...picker.querySelectorAll('[data-theme-choice]')] : [];
    if (!choices.length) return;
    const currentIndex = choices.indexOf(choice);
    let nextIndex = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % choices.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + choices.length) % choices.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = choices.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    choices[nextIndex].focus();
    choices[nextIndex].click();
  });

  systemTheme.addEventListener?.('change', () => {
    if (root.dataset.themePreference === 'auto') applyPreference('auto');
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && root.dataset.themePreference === 'auto') applyPreference('auto');
  });
})();
