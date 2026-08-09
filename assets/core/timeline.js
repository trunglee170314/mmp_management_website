(() => {
  const dates = window.MMPTimeline;
  const geometry = window.MMPTimelineLayout;
  if (!dates || !geometry) return;
  let root, viewport, controller, rangeController, rowObserver, rangeTimer, taskFormController;
  let pending = false, rangeBusy = false, rowBusy = false, gesture = null, frame = 0, generation = 0;
  let milestones = [], groups = [], holidays = [], cursors = [0], nextCursor = '', menuRange = null, undo = [];
  let suppressClickUntil = 0, taskFormGeneration = 0, automaticRows = true, focusTask = '', unscheduledBusy = false;
  let tasks = [], treeExpanded = false, calendarKey = '', scrollFrame = 0;
  let markerClusters = new Map();
  const collapsedMilestones = new Set();
  const collapsedTasks = new Set();
  let taskLayouts = new Map();
  const versions = new Map();
  const $ = selector => root?.querySelector(selector);
  const all = selector => [...root.querySelectorAll(selector)];
  const make = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const open = dialog => { if (dialog && !dialog.open) dialog.showModal(); };
  const close = dialog => dialog?.close();
  const dayWidth = () => parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width')) || 42;
  const treeWidth = () => treeExpanded ? Math.min(300, viewport.clientWidth * .42) : 0;
  const calendarWidth = () => Math.max(1, viewport.clientWidth - treeWidth());
  const origin = () => root.dataset.windowStart;
  const count = () => Number(root.dataset.dayCount);
  const clampDay = day => Math.max(0, Math.min(count() - 1, day));
  const minDay = dates.ordinal('0001-01-01'), maxDay = dates.ordinal('9999-12-31');
  const safeDay = day => Math.max(minDay, Math.min(maxDay, day));
  const hideFeedback = () => {
    const box = $('[data-timeline-feedback]');
    if (box?.contains(document.activeElement)) viewport.focus({preventScroll: true});
    if (box) box.hidden = true;
  };
  const message = (text, error = false) => {
    if (!error) { hideFeedback(); return; }
    const box = $('[data-timeline-feedback]'); box.hidden = false; box.classList.toggle('is-error', error);
    $('[data-feedback-text]').textContent = text;
  };
  const syncCalendarBusy = () => {
    all('[data-calendar-day],[data-task-bar],[data-milestone-id],[data-task-reorder-handle]').forEach(button => { button.disabled = pending || rangeBusy || rowBusy; });
    if (rangeBusy || rowBusy) viewport.setAttribute('aria-busy', 'true'); else viewport.removeAttribute('aria-busy');
  };
  const busy = value => { pending = value; root.classList.toggle('is-saving', value); root.setAttribute('aria-busy', String(value)); syncCalendarBusy(); };
  const formError = (form, text = '') => { const box = form.querySelector('[data-form-error]'); if (box) { box.hidden = !text; box.textContent = text; } };
  const dataFor = values => { const data = new FormData(); Object.entries(values).forEach(([key, value]) => Array.isArray(value) ? value.forEach(item => data.append(key, item)) : data.append(key, value ?? '')); return data; };
  const fill = (form, values) => Object.entries(values).forEach(([name, value]) => {
    [...form.querySelectorAll('[name]')].filter(input => input.name === name).forEach(input => {
      if (input.type === 'checkbox') input.checked = Array.isArray(value) ? value.map(String).includes(input.value) : !!value;
      else if (input.multiple) [...input.options].forEach(option => { option.selected = (value || []).map(String).includes(option.value); });
      else input.value = value ?? '';
    });
  });
  const fetchResponse = async (url, options = {}) => fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
  const post = async (url, body) => {
    const response = await fetchResponse(url, {method: 'POST', body, headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': $('[name="csrfmiddlewaretoken"]')?.value || ''}});
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('Unable to confirm the update. Check your session and reload.');
    const result = await response.json();
    if (!response.ok) {
      const details = Object.entries(result.errors || {}).map(([key, values]) => `${key}: ${[].concat(values).map(value => value.message || value).join(' ')}`).join(' · ');
      const error = new Error([result.error, details].filter(Boolean).join(' · ') || 'Unable to save.'); error.status = response.status; throw error;
    }
    return result;
  };
  const viewState = () => ({leftDay: dates.ordinal(origin()) + viewport.scrollLeft / dayWidth(), top: viewport.scrollTop,
    zoom: dayWidth(), period: $('[data-milestone-period]')?.value, important: $('[data-important-only]')?.checked,
    extentStart: origin(), extentEnd: root.dataset.windowEnd, treeExpanded, collapsedTasks: [...collapsedTasks],
    collapsed: all('.timeline-section.is-collapsed').map(section => section.dataset.group), cursors: [...cursors]});
  const restoreView = view => {
    viewport.scrollLeft = Math.max(0, (view.leftDay - dates.ordinal(origin())) * dayWidth()); viewport.scrollTop = view.top;
    all('[data-timeline-section]').forEach(section => { const collapsed = view.collapsed?.includes(section.dataset.group); section.classList.toggle('is-collapsed', !!collapsed); section.querySelector('[data-group-body]').hidden = !!collapsed; section.querySelector('[data-group-toggle]').setAttribute('aria-expanded', String(!collapsed)); });
    renderCalendar(); updatePeriod();
  };
  const chartUrl = (start, end, after = 0) => { const url = new URL(window.location.href); url.searchParams.delete('page'); url.searchParams.set('start', start); url.searchParams.set('end', end); url.searchParams.set('after', after); url.searchParams.set('fragment', 'chart'); if (focusTask) url.searchParams.set('focus_task', focusTask); return url; };
  const fetchChart = async (start, end, after, signal) => {
    const response = await fetchResponse(chartUrl(start, end, after), {signal});
    if (!response.ok) throw new Error('Unable to load the calendar. Retry or reload.');
    const fragment = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-chart-fragment]');
    if (!fragment) throw new Error('Your session may have expired. Reload before editing.');
    return document.importNode(fragment, true);
  };
  const updateMetadata = fragment => {
    for (const [name, update] of [['milestone', value => { milestones = value; }], ['group', value => { groups = value; }], ['holiday', value => { holidays = value; }], ['task', value => { tasks = value; }]]) {
      const script = fragment.querySelector(`#chart-${name}-data`); if (!script) throw new Error('Incomplete calendar data. Reload before editing.');
      const value = JSON.parse(script.textContent); if (!Array.isArray(value)) throw new Error('Invalid calendar data.'); update(value);
      const original = $(`#timeline-${name}-data`); if (original) original.textContent = script.textContent;
    }
    calendarKey = '';
  };
  const mergeRows = (target, source, targetRoot = root) => {
    source.querySelectorAll('[data-timeline-section]').forEach(section => {
      const existing = [...target.querySelectorAll('[data-timeline-section]')].find(item => item.dataset.group === section.dataset.group);
      if (!existing) { target.querySelector('[data-row-sentinel]').before(section.cloneNode(true)); return; }
      const body = existing.querySelector('[data-group-body]');
      section.querySelectorAll('[data-task-row]').forEach(row => { if (!body.querySelector(`[data-task-row="${row.dataset.taskRow}"]`)) body.querySelector('.timeline-group-create-space').before(row.cloneNode(true)); });
    });
    const template = source.querySelector('[data-unscheduled-page]'), list = targetRoot.querySelector('[data-unscheduled-list]');
    template?.content.querySelectorAll('[data-unscheduled-row]').forEach(row => { if (list && !list.querySelector(`[data-unscheduled-row="${row.dataset.unscheduledRow}"]`)) list.append(row.cloneNode(true)); });
    target.dataset.nextCursor = source.dataset.nextCursor;
    const button = target.querySelector('[data-load-more]'); if (button) button.hidden = !source.dataset.nextCursor;
  };
  const serverRange = () => {
    const center = safeDay(Math.floor(dates.ordinal(origin()) + (viewport.scrollLeft + calendarWidth() / 2) / dayWidth()));
    const start = Math.max(minDay, Math.min(maxDay - 240, center - 120));
    return [dates.date(start), dates.date(start + 240)];
  };
  const refresh = async () => {
    if (!root.isConnected) return;
    const view = viewState(), token = ++generation, returnFocus = root.contains(document.activeElement);
    rangeController?.abort(); rowObserver?.disconnect(); rangeBusy = false; viewport.removeAttribute('aria-busy');
    const [start, end] = serverRange();
    const url = chartUrl(start, end); url.searchParams.delete('fragment'); url.searchParams.delete('after');
    const response = await fetchResponse(url);
    if (!response.ok) throw new Error('Saved, but unable to refresh. Reload to see the latest data.');
    const replacement = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-timeline-root]');
    if (!replacement) throw new Error('Saved, but your session may have expired. Reload the page.');
    const imported = document.importNode(replacement, true), chart = imported.querySelector('[data-chart-fragment]');
    const extra = await Promise.all(view.cursors.slice(1).map(after => fetchChart(start, end, after)));
    if (token !== generation) return;
    extra.forEach(fragment => mergeRows(chart, fragment, imported));
    root.replaceWith(imported); initialize(view); document.dispatchEvent(new CustomEvent('mmp:page-loaded', {detail: {root}})); if (returnFocus) viewport.focus({preventScroll: true});
  };
  const changeWindow = async (centerDay, force = false) => {
    if (pending || rowBusy) return;
    centerDay = safeDay(Math.floor(centerDay));
    const extent = geometry.unionExtent(origin(), root.dataset.windowEnd,
      dates.date(Math.max(minDay, centerDay - 180)), dates.date(Math.min(maxDay, centerDay + 180)));
    if (extent.start === origin() && extent.end === root.dataset.windowEnd) return;
    const oldStart = dates.ordinal(origin()), oldLeft = viewport.scrollLeft;
    root.dataset.windowStart = extent.start; root.dataset.windowEnd = extent.end;
    root.dataset.dayCount = dates.ordinal(extent.end) - dates.ordinal(extent.start) + 1;
    applyExtent(); viewport.scrollLeft = oldLeft + (oldStart - dates.ordinal(extent.start)) * dayWidth();
    if (gesture) {
      const added = oldStart - dates.ordinal(extent.start); gesture.scrollLeft += added * dayWidth(); if (gesture.kind === 'range') gesture.day += added;
      if (added) all('.timeline-milestone,.timeline-milestone-anchor,.timeline-milestone-leader').forEach(marker => { marker.style.left = `${parseFloat(marker.style.left) + added * dayWidth()}px`; });
    }
    calendarKey = ''; renderCalendar(); paintAllBars(); if (!gesture) renderMarkers(); updatePeriod();
    if (!gesture) {
      rangeController?.abort(); rangeController = new AbortController(); const token = ++generation;
      rangeBusy = true; syncCalendarBusy();
      try {
        const fragment = await fetchChart(...serverRange(), 0, rangeController.signal);
        if (token !== generation || gesture || pending) return;
        updateMetadata(fragment); mergeRows($('[data-chart-fragment]'), fragment); layoutRows(); renderMilestones(); renderCalendar(); renderMarkers();
      } catch (error) { if (error.name !== 'AbortError') message(error.message, true); }
      finally { if (token === generation) { rangeBusy = false; syncCalendarBusy(); observeRows(); } }
    }
  };
  const scrollToDate = async value => {
    const target = dates.ordinal(value), visibleDays = calendarWidth() / dayWidth();
    if (target < dates.ordinal(origin()) + visibleDays / 2 || target > dates.ordinal(root.dataset.windowEnd) - visibleDays / 2) await changeWindow(target, true);
    viewport.scrollLeft = Math.max(0, (target - dates.ordinal(origin())) * dayWidth() - calendarWidth() / 2); renderCalendar(); renderMarkers(); updatePeriod();
  };
  const updatePeriod = () => {
    const day = dates.date(safeDay(dates.ordinal(origin()) + Math.floor(viewport.scrollLeft / dayWidth())));
    const label = new Intl.DateTimeFormat('en', {month: 'long', year: 'numeric', timeZone: 'UTC'}).format(new Date(`${day}T00:00:00Z`));
    $('[data-current-period]').textContent = label;
  };
  const maybeWindow = () => {
    if (gesture || pending || rangeBusy || rowBusy || root.querySelector('dialog[open]')) return;
    const left = viewport.scrollLeft / dayWidth(), right = (viewport.scrollLeft + calendarWidth()) / dayWidth();
    if ((left < 25 && dates.ordinal(origin()) > minDay) || (right > count() - 25 && dates.ordinal(root.dataset.windowEnd) < maxDay)) changeWindow(dates.ordinal(origin()) + (left < 25 ? left - 80 : right + 80));
  };
  const loadRows = async requestedAfter => {
    if (!nextCursor || pending || rowBusy || rangeBusy || gesture) return;
    rowBusy = true; const after = Number.isFinite(requestedAfter) ? requestedAfter : Number(nextCursor), token = generation;
    syncCalendarBusy();
    const anchor = all('[data-task-row]').find(row => row.getBoundingClientRect().bottom > viewport.getBoundingClientRect().top + 70), anchorTop = anchor?.getBoundingClientRect().top;
    try {
      const fragment = await fetchChart(...serverRange(), after);
      if (token !== generation) return;
      const chartRowsBefore = all('[data-task-row]').length;
      mergeRows($('[data-chart-fragment]'), fragment); updateMetadata(fragment); if (!cursors.includes(after)) cursors.push(after);
      automaticRows = all('[data-task-row]').length > chartRowsBefore;
      if (anchor?.isConnected) viewport.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
      renderMilestones(); layoutRows(); renderCalendar(); renderMarkers();
    } catch (error) { message(error.message, true); }
    finally { rowBusy = false; syncCalendarBusy(); observeRows(); }
  };
  const observeRows = () => {
    rowObserver?.disconnect();
    if (!nextCursor || !automaticRows || !window.IntersectionObserver) return;
    rowObserver = new IntersectionObserver(entries => {
      const entry = entries.find(entry => entry.isIntersecting && entry.target.matches('[data-row-placeholder]'));
      if (entry) loadRows(Number(entry.target.dataset.rowPlaceholder) - 1);
      else if (entries.some(entry => entry.isIntersecting)) loadRows();
    }, {root: viewport, rootMargin: '80px'});
    const sentinel = $('[data-row-sentinel]'); if (sentinel) rowObserver.observe(sentinel);
    all('[data-row-placeholder]').forEach(row => rowObserver.observe(row));
  };
  const loadUnscheduled = async button => {
    if (pending || unscheduledBusy || !button.dataset.nextCursor) return;
    unscheduledBusy = true; button.disabled = true; const token = generation;
    try {
      const url = chartUrl(origin(), root.dataset.windowEnd); url.searchParams.set('fragment', 'unscheduled'); url.searchParams.set('unscheduled_after', button.dataset.nextCursor);
      const response = await fetchResponse(url), fragment = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-unscheduled-fragment]');
      if (!response.ok || !fragment) throw new Error('Unable to load unscheduled tasks.');
      if (token !== generation) return;
      fragment.querySelectorAll('[data-unscheduled-row]').forEach(row => { if (!$(`[data-unscheduled-row="${row.dataset.unscheduledRow}"]`)) $('[data-unscheduled-list]').append(document.importNode(row, true)); });
      button.dataset.nextCursor = fragment.dataset.nextCursor; button.hidden = !button.dataset.nextCursor;
    } catch (error) { message(error.message, true); } finally { unscheduledBusy = false; button.disabled = false; }
  };
  const zoom = (width, center = true) => {
    if (gesture || rangeBusy) return;
    const day = (viewport.scrollLeft + calendarWidth() / 2) / dayWidth(); width = Math.max(16, Math.min(72, width));
    viewport.style.setProperty('--day-width', `${width}px`); viewport.classList.toggle('is-zoomed-out', width < 24);
    $('[data-timeline-zoom-label]').textContent = `${Math.round(width / 42 * 100)}%`; $('[data-timeline-zoom-out]').disabled = width <= 16; $('[data-timeline-zoom-in]').disabled = width >= 72;
    applyExtent();
    if (center) viewport.scrollLeft = Math.max(0, day * width - calendarWidth() / 2);
    try { localStorage.setItem('mmp-timeline-day-width', String(width)); } catch (_) { /* Storage is optional. */ }
    if (tasks.length) layoutRows(); calendarKey = ''; renderCalendar(); renderMarkers(); updatePeriod();
  };
  const openTask = async (range = null, editUrl = null) => {
    if (pending || gesture) return;
    taskFormController?.abort(); taskFormController = new AbortController(); const token = ++taskFormGeneration;
    const dialog = $('#timeline-create-task-dialog'), container = $('[data-task-form-container]');
    const url = new URL(editUrl || root.dataset.taskCreateUrl, location.origin); url.searchParams.set('modal', '1');
    if (range) { url.searchParams.set('timeline_start_date', range.start); url.searchParams.set('due_date', range.due); if (range.group) url.searchParams.set('timeline_group', range.group); }
    container.replaceChildren(make('p', '', 'Loading…')); open(dialog);
    try {
      const response = await fetchResponse(url, {signal: taskFormController.signal}); const html = await response.text();
      if (token !== taskFormGeneration || !dialog.open) return;
      const doc = new DOMParser().parseFromString(html, 'text/html'), form = doc.querySelector('[data-shared-task-form]');
      if (!response.ok || !form) throw new Error('Unable to open Task. Check your session and reload.');
      container.replaceChildren(document.importNode(form, true)); document.dispatchEvent(new CustomEvent('mmp:page-loaded', {detail: {root: container}}));
      container.querySelector('[name="title"]')?.focus();
    } catch (error) { if (error.name === 'AbortError' || token !== taskFormGeneration || !dialog.open) return; container.replaceChildren(make('p', 'timeline-form-errors', error.message)); const button = make('button', 'button secondary', 'Close'); button.type = 'button'; button.dataset.dialogClose = ''; container.append(button); }
  };
  const ownerKind = () => {
    const form = $('[data-milestone-form]'), kind = $('[data-milestone-owner-kind]').value;
    $('[data-milestone-group-field]').hidden = kind !== 'group'; $('[data-milestone-task-field]').hidden = kind !== 'task';
    if (kind !== 'group') form.elements.timeline_group.value = ''; if (kind !== 'task') form.elements.task.value = '';
    form.querySelectorAll('[data-single-task-picker]').forEach(picker => picker.refreshSingleTaskPicker?.());
  };
  const updatePalette = () => {
    const form = $('[data-milestone-form]'), value = form.elements.color.value;
    all('[data-milestone-color]').forEach(button => { const selected = button.dataset.milestoneColor.toUpperCase() === value.toUpperCase(); button.setAttribute('aria-pressed', String(selected)); button.querySelector('[data-color-check]').textContent = selected ? '✓' : ''; button.disabled = form.elements.color.disabled; });
  };
  const openMilestone = (item = null, range = null) => {
    const form = $('[data-milestone-form]'); form.reset(); formError(form); form.querySelectorAll('[name]').forEach(input => { input.disabled = false; });
    [...form.elements.task.options].forEach(option => { option.hidden = false; });
    form.dataset.milestoneId = item?.id || ''; form.action = item?.edit_url || '/tasks/timeline/milestones/add/';
    const values = item ? {...item, task: item.task_id || item.task || '', timeline_group: item.group_id || item.timeline_group || '', color: item.color || ''} : {date: range?.start || root.dataset.today, timeline_group: range?.parentTask ? '' : range?.group || '', task: range?.parentTask || '', visibility: 'shared', color: '', is_important: false};
    for (const [name, value] of [['color', values.color], ['task', values.task]]) if (value && ![...form.elements[name].options].some(option => option.value === String(value))) form.elements[name].add(new Option(name === 'task' ? item.task_name || `Task #${value}` : 'Current color', value));
    (item?.scopes || []).forEach(id => { if (![...form.elements.scopes.options].some(option => option.value === String(id))) form.elements.scopes.add(new Option(item.scope_options?.find(scope => scope.id === id)?.name || `Scope #${id}`, id)); });
    fill(form, values); $('[data-milestone-owner-kind]').value = values.task ? 'task' : values.timeline_group ? 'group' : 'global'; ownerKind();
    if (item && !item.can_manage) form.querySelectorAll('[name]').forEach(input => { input.disabled = true; });
    else if (item?.can_change_visibility === false) form.elements.visibility.disabled = true;
    $('[data-milestone-title]').textContent = item ? 'Edit Milestone' : 'New Milestone'; $('[data-delete-milestone]').hidden = !item?.can_manage; $('[data-save-milestone]').hidden = !!item && !item.can_manage;
    updatePalette(); open($('#timeline-milestone-dialog')); form.elements.name.focus();
    document.dispatchEvent(new CustomEvent('mmp:page-loaded', {detail: {root: form}}));
    form.querySelectorAll('[data-single-task-picker]').forEach(picker => picker.refreshSingleTaskPicker?.());
  };
  const openAdmin = (kind, item = null, range = null) => {
    const form = $(`[data-${kind}-form]`); if (!form) return;
    form.reset(); formError(form); form.dataset.itemId = item?.id || ''; form.dataset.revision = item?.revision || '';
    form.action = `/tasks/timeline/${kind === 'group' ? 'groups' : 'holidays'}/${item ? `${item.id}/edit/` : 'add/'}`;
    if (item) fill(form, item); else if (kind === 'holiday') fill(form, {start_date: range?.start || root.dataset.today, end_date: range?.due || range?.start || root.dataset.today});
    updateAdminPalette(form);
    $(`[data-${kind}-dialog-title]`).textContent = `${item ? 'Edit' : 'New'} ${kind === 'group' ? 'Group' : 'Holiday'}`; $(`[data-delete-${kind}]`).hidden = !item;
    open($(`#timeline-${kind}-dialog`)); form.elements.name.focus();
  };
  const updateAdminPalette = form => {
    form.querySelectorAll('[data-admin-color]').forEach(button => {
      const selected = button.dataset.adminColor.toUpperCase() === form.elements.color.value.toUpperCase();
      button.setAttribute('aria-pressed', String(selected)); button.querySelector('[data-color-check]').textContent = selected ? '✓' : '';
    });
  };
  const renderMilestones = () => {
    const list = $('[data-milestone-list]'), mode = $('[data-milestone-period]').value, important = $('[data-important-only]').checked, today = root.dataset.today;
    list.replaceChildren();
    const items = milestones.filter(item => { const delta = dates.ordinal(item.date) - dates.ordinal(today); return (!important || item.is_important) && (mode === 'all' || mode === 'past' && delta < 0 || mode === 'today' && delta === 0 || ['7', '30'].includes(mode) && delta >= 0 && delta <= Number(mode)); }).sort((a, b) => mode === 'past' ? b.date.localeCompare(a.date) : a.date.localeCompare(b.date));
    const rowFor = item => {
      const row = make('div', 'timeline-overview-item'), jump = make('button', 'timeline-overview-jump');
      jump.type = 'button'; jump.dataset.jumpMilestone = item.id;
      const symbol = make('span', 'milestone-item-symbol', item.is_important ? '★' : '⚑');
      symbol.style.color = item.resolved_color || item.display_color || item.color || '#8B5CF6';
      const copy = make('span'); copy.append(make('strong', '', item.name), make('small', '', `${item.date}${item.visibility === 'private' ? ' · Private' : ''}`));
      jump.append(symbol, copy); row.append(jump);
      const edit = make('button', 'plain', item.can_manage ? 'Edit' : 'View'); edit.type = 'button'; edit.dataset.openMilestone = item.id; row.append(edit); return row;
    };
    const branch = (key, label, className) => {
      const details = make('details', `timeline-milestone-branch ${className}`);
      details.dataset.milestoneBranch = key; details.open = !collapsedMilestones.has(key);
      const summary = make('summary', '', label); summary.title = label; details.append(summary);
      // Only user changes update the persisted collapse state, not initial rendering.
      details.addEventListener('toggle', () => { if (details.isConnected) { if (details.open) collapsedMilestones.delete(key); else collapsedMilestones.add(key); } });
      return details;
    };
    const hierarchy = new Map();
    items.forEach(item => {
      const taskId = item.task_id || item.task, groupId = taskId ? item.task_group : item.group_id || item.timeline_group;
      const key = groupId ? `group:${groupId}` : taskId ? 'ungrouped' : 'global';
      if (!hierarchy.has(key)) hierarchy.set(key, {groupId, direct: [], tasks: new Map()});
      const entry = hierarchy.get(key);
      if (!taskId) entry.direct.push(item);
      else { if (!entry.tasks.has(String(taskId))) entry.tasks.set(String(taskId), []); entry.tasks.get(String(taskId)).push(item); }
    });
    const order = key => key === 'global' ? -1 : key === 'ungrouped' ? groups.length : groups.findIndex(group => `group:${group.id}` === key);
    [...hierarchy].sort(([a], [b]) => order(a) - order(b)).forEach(([key, entry]) => {
      const label = key === 'global' ? 'Timeline' : key === 'ungrouped' ? 'Group: Ungrouped' : `Group: ${groups.find(group => String(group.id) === String(entry.groupId))?.name || 'Group'}`;
      const section = branch(key, label, 'timeline-milestone-group');
      entry.direct.forEach(item => section.append(rowFor(item)));
      [...entry.tasks].sort(([, a], [, b]) => a[0].task_name.localeCompare(b[0].task_name)).forEach(([id, taskItems]) => {
        const task = branch(`task:${id}`, `Task: ${taskItems[0].task_name || `#${id}`}`, 'timeline-milestone-task');
        taskItems.forEach(item => task.append(rowFor(item))); section.append(task);
      });
      list.append(section);
    });
    if (!items.length) list.append(make('p', 'empty', 'No milestones.'));
    renderMarkers();
  };
  const visibleMilestones = () => {
    const nodes = geometry.hierarchy(tasks), currentIds = new Set(nodes.map(task => String(task.id)));
    const hidden = new Set(nodes.filter(task => task.ancestors.some(id => collapsedTasks.has(id))).map(task => String(task.id)));
    return milestones.filter(item => {
      if ($('[data-important-only]')?.checked && !item.is_important) return false;
      const id = item.task_id || item.task;
      return currentIds.has(String(id)) ? !hidden.has(String(id)) : !(item.task_ancestors || []).some(ancestor => currentIds.has(String(ancestor)) && collapsedTasks.has(String(ancestor)));
    });
  };
  const ownerPath = item => {
    const taskId = item.task_id || item.task, groupId = taskId ? item.task_group : item.group_id || item.timeline_group;
    const group = groupId ? groups.find(group => String(group.id) === String(groupId))?.name || 'Group' : taskId ? 'Ungrouped' : 'Timeline';
    return taskId ? `Group: ${group} → Task: ${item.task_name || `#${taskId}`}` : groupId ? `Group: ${group}` : 'Timeline';
  };
  const renderMarkers = () => {
    if (gesture) return;
    all('.timeline-marker-overlay').forEach(overlay => overlay.replaceChildren());
    const first = Math.max(0, Math.floor(viewport.scrollLeft / dayWidth()) - 3), last = Math.min(count() - 1, Math.ceil((viewport.scrollLeft + calendarWidth()) / dayWidth()) + 3);
    markerClusters = new Map(geometry.clusters(visibleMilestones()).map(cluster => [cluster.key, cluster]));
    const physicalOwners = new Map(), placed = new Map();
    markerClusters.forEach(cluster => {
      const item = cluster.items[0], taskId = item.task_id || item.task, groupId = taskId ? item.task_group : item.group_id || item.timeline_group;
      const key = taskId && item.task_has_schedule ? `task:${taskId}` : groupId ? `header:${groupId}` : 'header:global';
      if (!physicalOwners.has(key)) physicalOwners.set(key, []); physicalOwners.get(key).push(cluster);
    });
    physicalOwners.forEach(clusters => geometry.positionClusters(clusters, dayWidth()).forEach(cluster => placed.set(cluster.key, cluster)));
    markerClusters.forEach(cluster => {
      const position = placed.get(cluster.key), offset = dates.ordinal(origin()) * dayWidth();
      const left = dates.ordinal(cluster.date) - dates.ordinal(origin());
      if (position.right - offset < first * dayWidth() || Math.min(position.anchor, position.left) - offset > (last + 1) * dayWidth()) return;
      const item = cluster.items[0], taskId = item.task_id || item.task;
      let parent, className;
      if (taskId && item.task_has_schedule) { parent = $(`[data-task-row="${taskId}"] .timeline-track`); className = 'timeline-task-markers'; }
      else {
        const groupId = taskId ? item.task_group : item.group_id || item.timeline_group;
        parent = groupId ? all('[data-timeline-section]').find(section => section.dataset.group === String(groupId))?.querySelector('.timeline-group-row') : $('.timeline-calendar-head');
        className = groupId ? 'timeline-group-markers' : 'timeline-global-markers';
      }
      if (!parent) return;
      let overlay = parent.querySelector(':scope > .timeline-marker-overlay');
      if (!overlay) { overlay = make('div', `timeline-marker-overlay ${className}`); parent.append(overlay); }
      const important = cluster.items.some(item => item.is_important), multiple = cluster.items.length > 1;
      const flag = make('button', `timeline-milestone${important ? ' is-important' : ''}${multiple ? ' is-cluster' : ''}`); flag.type = 'button';
      if (multiple) flag.dataset.milestoneCluster = cluster.key;
      else { flag.dataset.milestoneId = item.id; flag.dataset.revision = item.revision; flag.dataset.canManage = String(item.can_manage); }
      flag.dataset.date = cluster.date; flag.dataset.owner = cluster.owner;
      flag.style.width = `${geometry.markerWidth(cluster.items.length)}px`;
      flag.style.left = `${position.left - offset}px`;
      flag.style.gridColumn = left + 1; flag.style.setProperty('--milestone-color', item.resolved_color || item.display_color || item.color || '#8B5CF6');
      const text = `${ownerPath(item)} · ${cluster.date} · ${multiple ? `${cluster.items.length} milestones` : item.name}`;
      flag.title = text; flag.setAttribute('aria-label', text);
      flag.append(make('span', '', important ? '★' : '⚑')); if (multiple) flag.append(make('b', '', String(cluster.items.length)));
      if (cluster.items.some(item => item.visibility === 'private')) flag.classList.add('has-private');
      if (Math.abs(position.left + geometry.markerWidth(cluster.items.length) / 2 - position.anchor) > .5) {
        const anchor = make('i', 'timeline-milestone-anchor'); anchor.style.left = `${position.anchor - offset}px`; anchor.style.setProperty('--milestone-color', flag.style.getPropertyValue('--milestone-color')); anchor.setAttribute('aria-hidden', 'true');
        const leader = make('i', 'timeline-milestone-leader'); leader.style.left = `${position.anchor - offset}px`; leader.style.width = `${position.left + geometry.markerWidth(cluster.items.length) / 2 - position.anchor}px`; leader.style.setProperty('--milestone-color', flag.style.getPropertyValue('--milestone-color')); leader.setAttribute('aria-hidden', 'true'); overlay.append(anchor, leader);
      }
      overlay.append(flag);
    });
  };
  const openCluster = key => {
    const cluster = markerClusters.get(key); if (!cluster) return;
    let dialog = $('#timeline-cluster-dialog');
    if (!dialog) {
      dialog = make('dialog', 'timeline-dialog'); dialog.id = 'timeline-cluster-dialog';
      const card = make('div', 'timeline-dialog-card'), header = make('header', 'timeline-dialog-head'), title = make('h2'); title.dataset.clusterTitle = '';
      const dismiss = make('button', '', '×'); dismiss.type = 'button'; dismiss.dataset.dialogClose = ''; dismiss.setAttribute('aria-label', 'Close milestone list');
      header.append(title, dismiss); card.append(header, make('div', 'timeline-cluster-list')); dialog.append(card); root.append(dialog);
      dialog.addEventListener('close', () => viewport.focus({preventScroll: true}));
    }
    dialog.querySelector('[data-cluster-title]').textContent = `${ownerPath(cluster.items[0])} · ${cluster.date}`;
    const list = dialog.querySelector('.timeline-cluster-list'); list.replaceChildren();
    cluster.items.forEach(item => {
      const row = make('div', 'timeline-overview-item'), symbol = make('span', 'milestone-item-symbol', item.is_important ? '★' : '⚑');
      const copy = make('span', 'timeline-cluster-copy'); copy.append(make('strong', '', item.name), make('small', '', `${item.date}${item.visibility === 'private' ? ' · Private' : ''}`));
      const edit = make('button', 'button secondary small-button', item.can_manage ? 'Edit' : 'View'); edit.type = 'button'; edit.dataset.openMilestone = item.id;
      row.append(symbol, copy, edit); list.append(row);
    });
    open(dialog);
  };
  const applyExtent = () => {
    viewport.style.setProperty('--calendar-width', `${count() * dayWidth()}px`);
    viewport.style.setProperty('--tree-width', `${treeWidth()}px`);
    viewport.style.setProperty('--timeline-days', String(count()));
    const weekday = new Date(`${origin()}T00:00:00Z`).getUTCDay();
    viewport.style.setProperty('--weekend-offset', String((6 - weekday + 7) % 7));
    root.classList.toggle('timeline-has-tree', treeExpanded);
    const toggle = $('[data-toggle-timeline-tree]');
    if (toggle) { toggle.setAttribute('aria-expanded', String(treeExpanded)); toggle.setAttribute('aria-label', treeExpanded ? 'Hide task tree' : 'Show task tree'); }
  };
  const renderCalendar = () => {
    if (!viewport) return;
    const first = Math.max(0, Math.floor(viewport.scrollLeft / dayWidth()) - 35), last = Math.min(count() - 1, Math.ceil((viewport.scrollLeft + calendarWidth()) / dayWidth()) + 35);
    const key = `${origin()}:${first}:${last}:${dayWidth()}:${treeWidth()}`; if (key === calendarKey) return; calendarKey = key;
    const head = $('.timeline-calendar-head'), monthGrid = head.querySelector('.timeline-month-grid'), dayGrid = head.querySelector('.timeline-day-grid');
    monthGrid.replaceChildren(); dayGrid.replaceChildren(); const holidayCells = [];
    const months = new Map();
    for (let index = first; index <= last; index++) {
      const value = dates.shift(origin(), index), date = new Date(`${value}T00:00:00Z`), matches = geometry.holidayOn(value, holidays);
      const cell = make('button', 'timeline-day-label'); cell.type = 'button'; cell.dataset.calendarDay = value; cell.append(make('span', '', String(date.getUTCDate())));
      cell.style.left = `${index * dayWidth()}px`; cell.style.width = `${dayWidth()}px`; cell.disabled = pending || rangeBusy || rowBusy; cell.title = `${value}${matches.length ? ` · ${matches.map(item => item.name).join(', ')}` : ''}`;
      cell.classList.toggle('is-today', value === root.dataset.today); cell.classList.toggle('is-week-start', date.getUTCDay() === 1); cell.classList.toggle('is-month-start', date.getUTCDate() === 1);
      cell.classList.toggle('is-weekend', [0, 6].includes(date.getUTCDay()));
      if (matches.length) { cell.classList.add('is-holiday'); cell.dataset.holidayId = matches[0].id; cell.style.setProperty('--holiday-color', matches[0].color); cell.append(make('i')); holidayCells.push({index, color: matches[0].color}); }
      dayGrid.append(cell);
      const monthKey = value.slice(0, 7); if (!months.has(monthKey)) months.set(monthKey, {first: index, last: index, date}); else months.get(monthKey).last = index;
    }
    months.forEach(month => {
      const segment = make('div', 'timeline-month-segment'); segment.style.left = `${month.first * dayWidth()}px`; segment.style.width = `${(month.last - month.first + 1) * dayWidth()}px`;
      segment.append(make('span', '', new Intl.DateTimeFormat('en', {month: 'short', year: 'numeric', timeZone: 'UTC'}).format(month.date))); monthGrid.append(segment);
    });
    let treeHead = head.querySelector('.timeline-tree-head'); if (!treeHead) { treeHead = make('div', 'timeline-tree-head', 'GROUP / TASK'); head.append(treeHead); }
    all('[data-group-body]').forEach(body => {
      let shade = body.querySelector('[data-body-holidays]'); if (!shade) { shade = make('div', 'timeline-body-holidays'); shade.dataset.bodyHolidays = ''; body.prepend(shade); }
      shade.replaceChildren(); holidayCells.forEach(holiday => { const cell = make('i'); cell.style.left = `${holiday.index * dayWidth()}px`; cell.style.width = `${dayWidth()}px`; cell.style.setProperty('--holiday-color', holiday.color); shade.append(cell); });
    });
    const today = $('.timeline-today-line-layer'); today.replaceChildren();
    const todayIndex = dates.ordinal(root.dataset.today) - dates.ordinal(origin());
    if (todayIndex >= 0 && todayIndex < count()) { const line = make('i'); line.style.left = `${(todayIndex + .5) * dayWidth()}px`; today.append(line); }
    if (!gesture) renderMarkers();
  };
  const layoutRows = () => {
    taskLayouts = new Map();
    const byId = new Map(tasks.map(task => [String(task.id), task]));
    const markerParents = new Set(milestones.filter(item => (!$('[data-important-only]')?.checked || item.is_important) && !byId.has(String(item.task_id || item.task))).flatMap(item => (item.task_ancestors || []).map(String)));
    all('[data-task-row]').forEach(row => {
      const task = byId.get(row.dataset.taskRow); if (!task) { row.remove(); return; }
      const section = all('[data-timeline-section]').find(section => section.dataset.group === String(task.group)); if (!section) { row.remove(); return; }
      const body = section.querySelector('[data-group-body]'); if (row.parentElement !== body) body.querySelector('.timeline-group-create-space').before(row);
      const bar = row.querySelector('[data-task-bar]');
      Object.assign(bar.dataset, {start: task.start, due: task.due, group: String(task.group), revision: task.revision, title: task.title, orderRevision: task.order_revision || ''});
      bar.querySelector('.timeline-bar-title').textContent = task.title; bar.title = `${task.title} · ${task.start || task.due} → ${task.due || task.start}`;
      ['todo', 'in_progress', 'done', 'paused', 'cancelled'].forEach(status => bar.classList.toggle(`status-${status}`, task.status === status));
      row.querySelector('.timeline-track').dataset.group = String(task.group); row.querySelector('.timeline-track').style.removeProperty('--weekend-offset');
      row.querySelectorAll('.timeline-track-holiday').forEach(cell => cell.remove());
      row.querySelector('.timeline-offscreen-task')?.remove();
    });
    all('[data-row-placeholder]').forEach(row => row.remove());
    all('[data-timeline-section]').forEach(section => {
      const body = section.querySelector('[data-group-body]'), groupTasks = tasks.filter(task => String(task.group) === section.dataset.group);
      body.dataset.createTrack = ''; body.dataset.group = section.dataset.group;
      const packed = geometry.pack(groupTasks, visibleMilestones(), dayWidth(), treeExpanded, collapsedTasks);
      body.style.height = `${packed.count * 52 + 14}px`;
      const ranks = new Map(packed.order.map((id, rank) => [id, rank]));
      groupTasks.slice().sort((a, b) => (ranks.get(String(a.id)) ?? Infinity) - (ranks.get(String(b.id)) ?? Infinity)).forEach(task => {
        const position = packed.positions.get(String(task.id)); let row = body.querySelector(`[data-task-row="${task.id}"]`);
        if (!position) { if (row) row.hidden = true; return; }
        position.hasChildren ||= markerParents.has(String(task.id));
        if (!row) { row = make('div', 'timeline-row-placeholder'); row.dataset.rowPlaceholder = task.id; body.querySelector('.timeline-group-create-space').before(row); }
        else body.querySelector('.timeline-group-create-space').before(row);
        row.hidden = false; taskLayouts.set(String(task.id), {task, position, section, body, row});
        row.style.top = `${position.lane * 52}px`; row.style.setProperty('--task-depth', String(position.depth));
        let label = row.querySelector('.timeline-task-label');
        if (!label) { label = make('div', 'timeline-task-label'); row.prepend(label); }
        label.replaceChildren(); label.classList.toggle('has-subtasks', position.hasChildren);
        const path = make('span', 'timeline-tree-path'); path.setAttribute('aria-hidden', 'true');
        position.branchLast.forEach((last, index) => { const column = make('i', `timeline-tree-column${index === position.branchLast.length - 1 ? ` is-branch${last ? ' is-last' : ''}` : last ? '' : ' is-through'}`); path.append(column); });
        label.append(path);
        if (position.hasChildren) {
          const toggle = make('button', 'timeline-subtask-toggle'); toggle.type = 'button'; toggle.dataset.toggleTask = task.id;
          const icon = make('span', 'timeline-disclosure-icon'); icon.setAttribute('aria-hidden', 'true'); toggle.append(icon);
          toggle.setAttribute('aria-expanded', String(!collapsedTasks.has(String(task.id)))); toggle.setAttribute('aria-label', `${collapsedTasks.has(String(task.id)) ? 'Expand' : 'Collapse'} subtasks of ${task.title}`); label.append(toggle);
        } else label.append(make('span', 'timeline-subtask-spacer'));
        const name = make(row.dataset.taskRow ? 'button' : 'span', 'timeline-task-label-name', task.title); name.title = task.title;
        if (row.dataset.taskRow) { name.type = 'button'; name.dataset.editTask = ''; name.dataset.editAction = row.querySelector('[data-task-bar]').dataset.editAction; }
        label.append(name);
        if (row.dataset.taskRow) {
          const handle = make('button', 'timeline-reorder-handle', '⠿'); handle.type = 'button'; handle.dataset.taskReorderHandle = task.id;
          handle.title = 'Drag to move or reorder · Alt + ↑ / ↓'; handle.setAttribute('aria-label', `Move or reorder ${task.title}`); label.append(handle);
        }
      });
      body.querySelector('.timeline-group-create-space').style.top = `${packed.count * 52}px`;
    });
    const loaded = new Set(all('[data-task-row]').map(row => row.dataset.taskRow));
    const missing = tasks.find(task => taskLayouts.has(String(task.id)) && !loaded.has(String(task.id))); nextCursor = missing ? String(Number(missing.id) - 1) : '';
    const more = $('[data-load-more]'); if (more) more.hidden = !nextCursor;
    automaticRows = true; applyExtent(); paintAllBars();
  };
  const paintAllBars = () => all('[data-task-bar]').forEach(bar => paint(bar, state(bar)));
  const state = bar => ({id: bar.dataset.taskId, start: bar.dataset.start || '', due: bar.dataset.due || '', group: bar.dataset.group || '', revision: bar.dataset.revision, order_revision: bar.dataset.orderRevision || '', url: bar.dataset.action});
  const paint = (bar, value) => {
    const rawLeft = dates.ordinal(value.start || value.due) - dates.ordinal(origin()), rawRight = dates.ordinal(value.due || value.start) - dates.ordinal(origin());
    const left = Math.max(0, rawLeft), right = Math.min(count() - 1, rawRight);
    bar.hidden = rawRight < 0 || rawLeft >= count();
    bar.style.gridColumn = `${left + 1} / span ${Math.max(1, right - left + 1)}`;
    bar.style.left = `${left * dayWidth() + 3}px`; bar.style.width = `${Math.max(6, (right - left + 1) * dayWidth() - 6)}px`;
    bar.classList.toggle('clipped-start', rawLeft < 0); bar.classList.toggle('clipped-end', rawRight >= count());
  };
  const mutation = async (kind, before, after, isUndo = false) => {
    if (kind === 'order' || kind === 'parent') {
      const values = kind === 'parent' ? after.restore ? {restore: after.restore} : {parent_id: after.parent_id || '', anchor_id: after.anchor_id || '', placement: after.placement || 'after'} : {anchor_id: after.anchor_id, placement: after.placement};
      const result = await post(`/tasks/timeline/tasks/${before.id}/${kind}/`, dataFor({...values, revision: before.revision, order_revision: before.order_revision}));
      const saved = result.task;
      if (!saved?.revision || !result.order_revision) throw new Error('Unable to confirm the saved task order. Reload before editing.');
      versions.set(`task:${before.id}`, saved.revision); versions.set(`order:${before.group}`, result.order_revision);
      if (!isUndo && result.updated && result.undo) undo.push({kind, before: {...before, ...result.undo}, after: {...saved, order_revision: result.order_revision}});
      return saved;
    }
    const values = kind === 'task' ? {timeline_start_date: after.start, due_date: after.due, timeline_group: after.group || '', revision: before.revision} : {date_only: '1', date: after.date, revision: before.revision};
    const result = await post(kind === 'task' ? before.url || `/tasks/timeline/tasks/${before.id}/` : before.edit_url, dataFor(values)), saved = result[kind];
    if (!saved?.revision) throw new Error('Saved without a revision. Reload before editing.'); versions.set(`${kind}:${before.id}`, saved.revision); if (!isUndo) undo.push({kind, before, after: {...after, ...saved}}); return saved;
  };
  const runUndo = async () => { if (pending || rangeBusy || gesture || !undo.length) return; const entry = undo.at(-1); busy(true); try { await mutation(entry.kind, {...entry.before, revision: versions.get(`${['order', 'parent'].includes(entry.kind) ? 'task' : entry.kind}:${entry.before.id}`) || entry.after.revision, order_revision: versions.get(`order:${entry.before.group}`) || entry.after.order_revision}, entry.before, true); undo.pop(); await refresh(); message('Undone.'); } catch (error) { if (error.status === 409) undo = []; message(error.message, true); } finally { busy(false); } };
  const tooltip = (event, text) => { const box = $('[data-drag-tooltip]'); box.hidden = false; box.textContent = text; box.style.left = `${Math.max(8, Math.min(innerWidth - 280, event.clientX + 12))}px`; box.style.top = `${Math.max(8, Math.min(innerHeight - 75, event.clientY + 18))}px`; };
  const dayAt = event => clampDay(Math.floor((event.clientX - viewport.getBoundingClientRect().left - treeWidth() + viewport.scrollLeft) / dayWidth()));
  const dropGroup = event => {
    const rect = viewport.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX >= rect.right - 12 || event.clientY < rect.top + 58 || event.clientY >= rect.bottom - 12) return null;
    const section = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-timeline-section]');
    return section && viewport.contains(section) ? section : null;
  };
  const findOrderTarget = (event, current, section) => {
    if (!section || section.dataset.group !== current.before.group) return null;
    const source = tasks.find(task => String(task.id) === String(current.before.id)); if (!source) return null;
    const entries = geometry.hierarchy(tasks.filter(task => String(task.group) === current.before.group));
    const blockIds = new Set(entries.filter(task => String(task.id) === String(source.id) || task.ancestors.includes(String(source.id))).map(task => String(task.id)));
    const body = section.querySelector('[data-group-body]'), bodyRect = body.getBoundingClientRect();
    const lane = Math.floor((event.clientY - bodyRect.top) / 52); if (lane < 0 || body.hidden) return null;
    const candidates = [...taskLayouts.values()].filter(entry => entry.section === section && entry.position.lane === lane && !blockIds.has(String(entry.task.id)));
    if (!candidates.length) return null;
    const hit = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-task-row]')?.dataset.taskRow;
    const centerDay = dates.ordinal(origin()) + (event.clientX - viewport.getBoundingClientRect().left - treeWidth() + viewport.scrollLeft) / dayWidth();
    const anchor = candidates.find(entry => String(entry.task.id) === hit) || candidates.slice().sort((a, b) => Math.abs(dates.ordinal(a.task.start || a.task.due) - centerDay) - Math.abs(dates.ordinal(b.task.start || b.task.due) - centerDay))[0];
    const placement = treeExpanded ? (event.clientY - bodyRect.top - lane * 52 < 26 ? 'before' : 'after') : centerDay < dates.ordinal(anchor.task.start || anchor.task.due) ? 'before' : 'after';
    const reordered = geometry.reorder(entries, source.id, anchor.task.id, placement);
    let error = reordered ? '' : 'Keep this task under its current parent.';
    if (!error && !treeExpanded) {
      const others = candidates.flatMap(entry => geometry.taskFootprint(entry.task, geometry.clusters(visibleMilestones().filter(item => String(item.task_id || item.task) === String(entry.task.id))), dayWidth()));
      const occupied = geometry.taskFootprint(source, geometry.clusters(visibleMilestones().filter(item => String(item.task_id || item.task) === String(source.id))), dayWidth());
      if (geometry.overlaps(occupied, others.sort((a, b) => a[0] - b[0]))) error = 'Cannot join this row: tasks or milestones overlap.';
    }
    return {anchor_id: anchor.task.id, placement, anchor, error};
  };
  const findTreeTarget = (event, current, section) => {
    if (!treeExpanded || !section || section.dataset.group !== current.before.group) return null;
    const rect = viewport.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX >= rect.left + treeWidth()) return null;
    const source = tasks.find(task => String(task.id) === String(current.before.id)); if (!source) return null;
    const entries = tasks.filter(task => String(task.group) === current.before.group);
    const body = section.querySelector('[data-group-body]'), y = event.clientY - body.getBoundingClientRect().top;
    const lane = Math.floor(y / 52), anchor = [...taskLayouts.values()].find(entry => entry.section === section && entry.position.lane === lane);
    let parentId = '', anchorId = '', placement = 'after', element, label;
    const header = section.querySelector('.timeline-group-row').getBoundingClientRect();
    if (event.clientY >= header.top && event.clientY <= header.bottom || !body.hidden && y >= parseFloat(body.style.height) - 14) {
      element = section.querySelector('.timeline-group-heading'); label = 'Move outside parent';
    } else if (anchor && !body.hidden) {
      const offset = y - lane * 52;
      if (offset >= 13 && offset <= 39) {
        parentId = anchor.task.id; element = anchor.row.querySelector('.timeline-task-label'); label = `Inside ${anchor.task.title}`;
      } else {
        parentId = anchor.task.order_parent ?? geometry.hierarchy(entries).find(task => String(task.id) === String(anchor.task.id)).effectiveParent;
        anchorId = anchor.task.id; placement = offset < 13 ? 'before' : 'after'; label = `${placement === 'before' ? 'Before' : 'After'} ${anchor.task.title}`;
      }
    } else return null;
    const preview = geometry.reparent(entries, source.id, parentId, anchorId, placement);
    return {kind: 'parent', parent_id: parentId || '', anchor_id: anchorId || '', placement, anchor, element, label,
      error: preview ? '' : 'Cannot move a task into itself or its descendants.'};
  };
  const updateGesture = event => {
    const current = gesture; if (!current) return; current.event = {clientX: event.clientX, clientY: event.clientY};
    const dx = event.clientX - current.x + viewport.scrollLeft - current.scrollLeft;
    if (!current.moved && Math.max(Math.abs(dx), Math.abs(event.clientY - current.y)) < 5) return; current.moved = true;
    const inTree = treeExpanded && event.clientX < viewport.getBoundingClientRect().left + treeWidth();
    const shift = inTree ? 0 : Math.round(dx / dayWidth());
    try {
      if (current.kind === 'task') {
        const section = dropGroup(event), vertical = Math.abs(event.clientY - current.y) > 10;
        if (!current.axisIntent && current.mode === 'move') {
          const dy = Math.abs(event.clientY - current.y), horizontal = Math.abs(event.clientX - current.x);
          if (vertical && dy > horizontal * 1.2) current.axisIntent = 'order';
          else if (horizontal > 10 && horizontal > dy * 1.2) current.axisIntent = 'dates';
        }
        const sourceLane = taskLayouts.get(String(current.before.id))?.position.lane;
        const targetLane = section ? Math.floor((event.clientY - section.querySelector('[data-group-body]').getBoundingClientRect().top) / 52) : null;
        const treeIntent = inTree && section?.dataset.group === current.before.group && ['move', 'order'].includes(current.mode);
        current.orderIntent = treeIntent || current.mode === 'order' || current.mode === 'move' && current.axisIntent === 'order' && section?.dataset.group === current.before.group && targetLane !== sourceLane;
        current.orderTarget = current.orderIntent ? treeIntent ? findTreeTarget(event, current, section) : findOrderTarget(event, current, section) : null;
        all('.timeline-order-drop-line').forEach(line => line.remove());
        all('.timeline-parent-drop-target').forEach(element => element.classList.remove('timeline-parent-drop-target', 'is-invalid'));
        if (current.orderTarget) {
          if (current.orderTarget.element) {
            current.orderTarget.element.classList.add('timeline-parent-drop-target');
            current.orderTarget.element.classList.toggle('is-invalid', !!current.orderTarget.error);
          } else {
            const line = make('div', `timeline-order-drop-line${current.orderTarget.error ? ' is-invalid' : ''}${treeIntent ? ' is-tree-drop' : ''}`);
            line.style.top = `${current.orderTarget.anchor.position.lane * 52 + (current.orderTarget.placement === 'after' ? 50 : 1)}px`; current.orderTarget.anchor.body.append(line);
          }
        }
        current.after = {...current.before, ...(current.orderIntent || current.mode === 'move' && shift === 0 ? {} : dates.range(current.before.start, current.before.due, shift, current.mode))};
        if (current.mode === 'move' || current.mode === 'order') {
          const section = dropGroup(event);
          current.after.group = current.mode === 'order' ? current.before.group : section ? section.dataset.group : current.before.group;
          all('.timeline-section.is-drop-target').forEach(item => item.classList.remove('is-drop-target'));
          section?.classList.add('is-drop-target');
          if (Math.abs(event.clientY - current.y) > 8 || viewport.scrollTop !== current.scrollTop) {
            if (!current.ghost) {
              current.ghost = make('div', 'timeline-drag-ghost', current.bar.dataset.title);
              const style = getComputedStyle(current.bar); current.ghost.style.background = style.backgroundColor; current.ghost.style.color = style.color;
              root.append(current.ghost); current.bar.classList.add('is-transfer-source');
            }
            current.ghost.style.left = `${Math.max(8, Math.min(innerWidth - 200, event.clientX - 30))}px`;
            current.ghost.style.top = `${event.clientY - 12}px`;
          }
        }
        paint(current.bar, current.after);
        const groupName = groups.find(group => String(group.id) === String(current.after.group))?.name || 'Ungrouped';
        tooltip(event, current.orderIntent ? current.orderTarget?.error || current.orderTarget?.label || (current.orderTarget ? `${current.orderTarget.placement === 'after' ? 'After' : 'Before'} ${current.orderTarget.anchor.task.title}` : 'Drop before or after a task in this group') : `${current.after.start || current.after.due} → ${current.after.due || current.after.start}${current.mode === 'move' ? ` · ${groupName}` : ''}`);
      }
      else if (current.kind === 'milestone') { current.after = {...current.before, date: dates.shift(current.before.date, shift)}; current.bar.style.gridColumn = dates.ordinal(current.after.date) - dates.ordinal(origin()) + 1; current.bar.style.left = `${(dates.ordinal(current.after.date) - dates.ordinal(origin()) + .5) * dayWidth() - parseFloat(current.bar.style.width) / 2}px`; tooltip(event, current.after.date); }
      else { const day = dayAt(event); current.after = {...dates.selectedRange(origin(), current.day, day), group: current.group}; current.preview.style.left = `${Math.min(day, current.day) * dayWidth()}px`; current.preview.style.width = `${(Math.abs(day - current.day) + 1) * dayWidth()}px`; tooltip(event, `${current.after.start} → ${current.after.due}`); }
    } catch (_) { /* Keep the last valid preview at the supported date boundary. */ }
  };
  const autoScroll = () => {
    frame = 0; if (!gesture?.moved) return;
    const rect = viewport.getBoundingClientRect(), event = gesture.event;
    const horizontalIntent = gesture.mode !== 'order' && !gesture.orderIntent && (gesture.kind !== 'task' || gesture.mode !== 'move' || Math.abs(event.clientX - gesture.x) >= 5);
    const dx = horizontalIntent ? (event.clientX > rect.right - 45 ? 14 : event.clientX < rect.left + 45 ? -14 : 0) : 0;
    const dy = gesture.kind === 'task' && ['move', 'order'].includes(gesture.mode) ? (event.clientY > rect.bottom - 35 ? 10 : event.clientY < rect.top + 90 ? -10 : 0) : 0;
    if (dx || dy) {
      viewport.scrollLeft += dx; viewport.scrollTop += dy;
      if (dx && !rowBusy) {
        const edge = viewport.scrollLeft / dayWidth();
        if (edge < 10 || edge + calendarWidth() / dayWidth() > count() - 10) changeWindow(dates.ordinal(origin()) + (dx < 0 ? edge - 80 : edge + calendarWidth() / dayWidth() + 80));
      }
      renderCalendar(); updateGesture(event);
    }
    frame = requestAnimationFrame(autoScroll);
  };
  const cancelGesture = (repaint = true) => {
    if (!gesture) return;
    if (gesture.bar) {
      if (gesture.kind === 'task') paint(gesture.bar, gesture.before);
      else { const index = dates.ordinal(gesture.before.date) - dates.ordinal(origin()); gesture.bar.style.gridColumn = index + 1; gesture.bar.style.left = `${(index + .5) * dayWidth() - parseFloat(gesture.bar.style.width) / 2}px`; }
      gesture.bar.classList.remove('is-transfer-source');
    }
    gesture.preview?.remove(); gesture.ghost?.remove(); all('.timeline-order-drop-line').forEach(line => line.remove()); all('.timeline-section.is-drop-target').forEach(section => section.classList.remove('is-drop-target'));
    all('.timeline-parent-drop-target').forEach(element => element.classList.remove('timeline-parent-drop-target', 'is-invalid'));
    viewport.classList.remove('is-dragging'); $('[data-drag-tooltip]').hidden = true;
    if (viewport.hasPointerCapture(gesture.pointerId)) viewport.releasePointerCapture(gesture.pointerId);
    gesture = null; cancelAnimationFrame(frame); frame = 0; if (repaint !== false) renderMarkers();
  };
  const startGesture = event => {
    if (event.button !== 0 || !event.isPrimary || pending || rangeBusy || rowBusy || event.target.closest('[data-group-toggle],[data-edit-group],[data-load-more]')) return;
    const handle = event.target.closest('[data-task-reorder-handle]');
    if (treeExpanded && event.clientX < viewport.getBoundingClientRect().left + treeWidth() && !handle) return;
    const button = event.target.closest('button'); if (button && !button.matches('[data-task-bar],[data-milestone-id],[data-calendar-day],[data-task-reorder-handle]')) return;
    const bar = handle ? handle.closest('[data-task-row]').querySelector('[data-task-bar]') : event.target.closest('[data-task-bar],[data-milestone-id]'), track = event.target.closest('[data-create-track]') || (event.target === viewport ? $('.timeline-calendar') : null), header = event.target.closest('[data-calendar-day]'); if (!bar && !track && !header) return;
    if (bar?.dataset.taskId) gesture = {kind: 'task', before: state(bar), mode: handle ? 'order' : event.target.closest('[data-resize]')?.dataset.resize || 'move'};
    else if (bar?.dataset.milestoneId) { const item = milestones.find(item => String(item.id) === bar.dataset.milestoneId); if (!item?.can_manage) return; gesture = {kind: 'milestone', before: {...item}}; }
    else {
      const preview = make('div', 'timeline-range-preview'), target = header ? header.parentElement : track;
      const rect = target.getBoundingClientRect(), lane = target.matches('[data-group-body]') ? Math.max(0, Math.floor((event.clientY - rect.top) / 52)) : 0;
      preview.style.top = `${lane * 52 + 2}px`; preview.style.bottom = 'auto'; preview.style.height = `${Math.max(8, Math.min(48, rect.height - lane * 52 - 4))}px`;
      target.append(preview);
      const row = treeExpanded && target.matches('[data-group-body]') ? [...target.querySelectorAll('[data-task-row]')].find(row => parseFloat(row.style.top) === lane * 52) : null;
      gesture = {kind: 'range', day: dayAt(event), group: track?.dataset.group || '', parentTask: row?.dataset.taskRow || track?.dataset.parentTask || '', holidayId: header?.dataset.holidayId, preview};
    }
    Object.assign(gesture, {bar, originalColumn: bar?.style.gridColumn, originalLeft: bar?.style.left, x: event.clientX, y: event.clientY, event: {clientX: event.clientX, clientY: event.clientY}, scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop, pointerId: event.pointerId, moved: false}); viewport.focus({preventScroll: true}); viewport.setPointerCapture(event.pointerId); event.preventDefault();
  };
  const endGesture = async event => {
    const current = gesture; if (!current || event.pointerId !== current.pointerId) return; if (current.moved) updateGesture(event); const after = current.after; cancelGesture(false); suppressClickUntil = Date.now() + 350;
    if (!current.moved) {
      if (current.mode === 'order') return;
      if (current.kind === 'task') openTask(null, current.bar.dataset.editAction);
    else if (current.kind === 'milestone') openMilestone(current.before);
      else if (current.holidayId && root.dataset.admin === 'true') openAdmin('holiday', holidays.find(item => String(item.id) === current.holidayId));
      else { menuRange = {...dates.selectedRange(origin(), current.day, current.day), group: current.group, parentTask: current.parentTask}; const menu = $('[data-timeline-create-menu]'); menu.hidden = false; menu.style.left = `${Math.max(8, Math.min(innerWidth - 180, event.clientX))}px`; menu.style.top = `${Math.max(8, Math.min(innerHeight - 190, event.clientY))}px`; menu.querySelector('button').focus(); } return;
    }
    if (current.kind === 'range') { if (after) openTask(after); return; }
    if (current.kind === 'task' && current.orderIntent) {
      if (!current.orderTarget || current.orderTarget.error) { renderMarkers(); return; }
      busy(true);
      try {
        await mutation(current.orderTarget.kind || 'order', current.before, current.orderTarget);
        if (current.orderTarget.parent_id) { collapsedTasks.delete(String(current.orderTarget.parent_id)); try { localStorage.setItem(`mmp-timeline-collapsed-tasks:${root.dataset.userId || ''}`, JSON.stringify([...collapsedTasks])); } catch (_) { /* Optional. */ } }
        focusTask = current.before.id; await refresh();
      }
      catch (error) { if (error.status === 409) { undo = []; try { await refresh(); } catch (_) { /* Show original conflict. */ } } message(error.message, true); }
      finally { busy(false); }
      return;
    }
    if (!after || (current.kind === 'task' ? after.start === current.before.start && after.due === current.before.due && String(after.group) === String(current.before.group) : after.date === current.before.date)) return;
    if (current.kind === 'task') paint(current.bar, after); else current.bar.style.gridColumn = dates.ordinal(after.date) - dates.ordinal(origin()) + 1;
    busy(true); let committed = false;
    try { await mutation(current.kind, current.before, after); committed = true; if (current.kind === 'task') focusTask = current.before.id; await refresh(); message('Saved.'); }
    catch (error) { if (!committed && current.kind === 'task') paint(current.bar, current.before); if (error.status === 409) { undo = []; try { await refresh(); } catch (_) { /* Preserve the conflict error. */ } } message(error.message, true); }
    finally { busy(false); }
  };
  const submitForm = async (event, form) => {
    event.preventDefault(); if (pending) return; formError(form); const data = new FormData(form); if (form.dataset.revision) data.set('revision', form.dataset.revision);
    const enabled = [...form.elements].filter(input => !input.disabled); enabled.forEach(input => { input.disabled = true; }); busy(true);
    let committed = false;
    try {
      if (form.matches('[data-shared-task-form]')) {
        const response = await fetchResponse(form.action, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}});
        if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
          const html = await response.text(), replacement = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-shared-task-form]');
          if (response.status === 400 && replacement) { const container = $('[data-task-form-container]'); container.replaceChildren(document.importNode(replacement, true)); document.dispatchEvent(new CustomEvent('mmp:page-loaded', {detail: {root: container}})); container.querySelector('.field-error')?.closest('.form-field')?.querySelector('input,select,textarea')?.focus(); return; }
          throw new Error('Unable to confirm Task save. Check your session and reload.');
        }
        const result = await response.json(); if (!response.ok || !result.task?.revision) throw new Error(result.error || 'Unable to save Task.'); focusTask = result.task.id;
      } else await post(form.action, data);
      committed = true; close(form.closest('dialog')); await refresh(); message('Saved.');
    } catch (error) { if (committed) message(error.message, true); else formError(form, error.message); }
    finally { enabled.forEach(input => { input.disabled = false; }); busy(false); }
  };
  const deleteItem = async (kind, item, form) => {
    if (!item || pending || !confirm(`Delete this ${kind}?`)) return; busy(true);
    try { await post(kind === 'milestone' ? item.delete_url : `/tasks/timeline/${kind === 'group' ? 'groups' : 'holidays'}/${item.id}/delete/`, dataFor({revision: item.revision})); close(form.closest('dialog')); await refresh(); message('Deleted.'); }
    catch (error) { formError(form, error.message); } finally { busy(false); }
  };
  const initialize = restore => {
    controller?.abort(); controller = new AbortController(); rowObserver?.disconnect(); rangeBusy = false; rowBusy = false;
    root = document.querySelector('[data-timeline-root]'); if (!root) return; viewport = $('[data-timeline-scroll]'); automaticRows = true;
    const listen = (target, name, fn, options = {}) => target?.addEventListener(name, fn, {...options, signal: controller.signal});
    const json = id => { try { return JSON.parse($(id)?.textContent || '[]'); } catch (_) { return []; } };
    milestones = json('#timeline-milestone-data'); groups = json('#timeline-group-data'); holidays = json('#timeline-holiday-data'); tasks = json('#timeline-task-data'); calendarKey = '';
    treeExpanded = !!restore?.treeExpanded;
    if (!restore) { try { treeExpanded = localStorage.getItem('mmp-timeline-tree') === 'open'; } catch (_) { /* Optional. */ } }
    collapsedTasks.clear();
    try { (restore?.collapsedTasks || JSON.parse(localStorage.getItem(`mmp-timeline-collapsed-tasks:${root.dataset.userId || ''}`) || '[]')).filter(id => /^\d+$/.test(String(id))).forEach(id => collapsedTasks.add(String(id))); } catch (_) { /* Optional. */ }
    if (restore?.extentStart) { const extent = geometry.unionExtent(restore.extentStart, restore.extentEnd, origin(), root.dataset.windowEnd); root.dataset.windowStart = extent.start; root.dataset.windowEnd = extent.end; root.dataset.dayCount = dates.ordinal(extent.end) - dates.ordinal(extent.start) + 1; }
    cursors = restore?.cursors || [Number($('[data-chart-fragment]').dataset.after) || 0]; nextCursor = $('[data-chart-fragment]').dataset.nextCursor;
    let width = 42; try { width = Number(localStorage.getItem('mmp-timeline-day-width')) || 42; } catch (_) { /* Optional. */ }
    zoom(restore?.zoom || width, false); if (restore?.period) $('[data-milestone-period]').value = restore.period; $('[data-important-only]').checked = !!restore?.important; renderMilestones();
    layoutRows();
    requestAnimationFrame(() => { if (restore) restoreView(restore); else scrollToDate(root.dataset.today); renderCalendar(); renderMarkers(); observeRows(); });
    listen(viewport, 'scroll', () => { updatePeriod(); if (!scrollFrame) scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; renderCalendar(); }); clearTimeout(rangeTimer); rangeTimer = setTimeout(maybeWindow, 90); }, {passive: true});
    const resizeObserver = new ResizeObserver(() => { applyExtent(); calendarKey = ''; renderCalendar(); }); resizeObserver.observe(viewport); controller.signal.addEventListener('abort', () => resizeObserver.disconnect(), {once: true});
    listen(viewport, 'wheel', event => { if (!(event.ctrlKey || event.metaKey)) return; event.preventDefault(); zoom(dayWidth() + (event.deltaY < 0 ? 4 : -4)); }, {passive: false});
    listen(viewport, 'pointerdown', startGesture); listen(viewport, 'pointermove', event => { if (gesture?.pointerId !== event.pointerId) return; updateGesture(event); if (gesture.moved) { viewport.classList.add('is-dragging'); if (!frame) frame = requestAnimationFrame(autoScroll); } }); listen(viewport, 'pointerup', endGesture); listen(viewport, 'pointercancel', cancelGesture);
    listen(root, 'submit', event => { const form = event.target.closest('[data-shared-task-form],[data-milestone-form],[data-group-form],[data-holiday-form]'); if (form) submitForm(event, form); });
    listen(root, 'change', event => { if (event.target.matches('[data-milestone-period],[data-important-only]')) { renderMilestones(); layoutRows(); renderCalendar(); renderMarkers(); observeRows(); } if (event.target.matches('[data-milestone-owner-kind]')) ownerKind(); });
    listen(document, 'click', event => {
      if (!root.contains(event.target)) { $('[data-timeline-create-menu]').hidden = true; return; }
      const button = event.target.closest('button'); if (!button) return;
      if (button.matches('[data-dialog-close]')) { if (button.closest('#timeline-create-task-dialog')) { taskFormController?.abort(); taskFormGeneration++; } close(button.closest('dialog')); return; }
      if (pending || gesture) return;
      if (button.matches('[data-feedback-dismiss]')) hideFeedback();
      if (button.matches('[data-toggle-timeline-tree]')) { const left = viewport.scrollLeft; treeExpanded = !treeExpanded; try { localStorage.setItem('mmp-timeline-tree', treeExpanded ? 'open' : 'closed'); } catch (_) { /* Optional. */ } layoutRows(); viewport.scrollLeft = left; calendarKey = ''; renderCalendar(); renderMarkers(); observeRows(); }
      if (button.matches('[data-toggle-task]')) {
        const id = button.dataset.toggleTask; if (collapsedTasks.has(id)) collapsedTasks.delete(id); else collapsedTasks.add(id);
        try { localStorage.setItem(`mmp-timeline-collapsed-tasks:${root.dataset.userId || ''}`, JSON.stringify([...collapsedTasks])); } catch (_) { /* Optional. */ }
        layoutRows(); renderCalendar(); renderMarkers(); observeRows(); $(`[data-toggle-task="${id}"]`)?.focus({preventScroll: true});
      }
      if (button.matches('[data-scroll-today]')) scrollToDate(root.dataset.today);
      if (button.matches('[data-timeline-zoom-in]')) zoom(dayWidth() + 4); if (button.matches('[data-timeline-zoom-out]')) zoom(dayWidth() - 4);
      if (button.matches('[data-load-more]')) loadRows();
      if (button.matches('[data-load-unscheduled]')) loadUnscheduled(button);
      if (button.matches('[data-jump-task-date]')) scrollToDate(button.dataset.jumpTaskDate);
      if (button.matches('[data-group-toggle]')) { const section = button.closest('[data-timeline-section]'), body = section.querySelector('[data-group-body]'); body.hidden = !body.hidden; button.setAttribute('aria-expanded', String(!body.hidden)); section.classList.toggle('is-collapsed', body.hidden); }
      if (button.matches('[data-edit-group]')) { const item = groups.find(item => String(item.id) === button.dataset.editGroup); if (item) openAdmin('group', item); else message('Group changed. Reload before editing.', true); }
      if (button.matches('[data-open-holiday]')) { const item = holidays.find(item => String(item.id) === button.dataset.openHoliday); if (item) openAdmin('holiday', item); else message('Holiday changed. Reload before editing.', true); }
      if (button.matches('[data-edit-task]') || (button.matches('[data-task-bar]') && event.detail === 0)) openTask(null, button.dataset.editAction);
      if (button.matches('[data-calendar-day]') && event.detail === 0) { menuRange = {start: button.dataset.calendarDay, due: button.dataset.calendarDay}; const menu = $('[data-timeline-create-menu]'); menu.hidden = false; const box = button.getBoundingClientRect(); menu.style.left = `${Math.min(innerWidth - 180, box.left)}px`; menu.style.top = `${Math.min(innerHeight - 190, box.bottom)}px`; menu.querySelector('button').focus(); }
      if (button.matches('[data-create-kind]')) { $('[data-timeline-create-menu]').hidden = true; const kind = button.dataset.createKind; if (kind === 'task') openTask(menuRange); else if (kind === 'milestone') openMilestone(null, menuRange); else openAdmin(kind, null, menuRange); }
      if (button.matches('[data-milestone-color]')) { const form = $('[data-milestone-form]'); if (!form.elements.color.disabled) { form.elements.color.value = button.dataset.milestoneColor; updatePalette(); } }
      if (button.matches('[data-admin-color]')) { const form = button.closest('form'); form.elements.color.value = button.dataset.adminColor; updateAdminPalette(form); }
      if (button.matches('[data-open-milestone]')) { const item = milestones.find(item => String(item.id) === button.dataset.openMilestone); if (item) openMilestone(item); else message('Milestone changed. Reload before editing.', true); }
      if (button.matches('[data-milestone-cluster]')) openCluster(button.dataset.milestoneCluster);
      if (button.matches('[data-jump-milestone]')) { const item = milestones.find(item => String(item.id) === button.dataset.jumpMilestone); if (item) scrollToDate(item.date); }
      if (button.matches('[data-milestone-id]') && Date.now() >= suppressClickUntil) { const item = milestones.find(item => String(item.id) === button.dataset.milestoneId); if (item) openMilestone(item); else message('Milestone changed. Reload before editing.', true); }
      if (button.matches('[data-delete-milestone]')) deleteItem('milestone', milestones.find(item => String(item.id) === $('[data-milestone-form]').dataset.milestoneId), $('[data-milestone-form]'));
      for (const kind of ['group', 'holiday']) if (button.matches(`[data-delete-${kind}]`)) { const form = $(`[data-${kind}-form]`); deleteItem(kind, (kind === 'group' ? groups : holidays).find(item => String(item.id) === form.dataset.itemId), form); }
    });
    listen(document, 'keydown', event => {
      if (event.key === 'Escape') { cancelGesture(); $('[data-timeline-create-menu]').hidden = true; }
      const handle = event.target.closest('[data-task-reorder-handle]');
      if (handle && event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key) && !pending && !rowBusy && !rangeBusy && !gesture) {
        event.preventDefault(); const source = tasks.find(task => String(task.id) === handle.dataset.taskReorderHandle);
        const siblings = geometry.hierarchy(tasks.filter(task => String(task.group) === String(source.group))).filter(task => String(task.order_parent ?? task.effectiveParent) === String(source.order_parent ?? ''));
        const index = siblings.findIndex(task => String(task.id) === String(source.id)), anchor = siblings[index + (event.key === 'ArrowUp' ? -1 : 1)];
        if (anchor) {
          const bar = handle.closest('[data-task-row]').querySelector('[data-task-bar]'); busy(true);
          mutation('order', state(bar), {anchor_id: anchor.id, placement: event.key === 'ArrowUp' ? 'before' : 'after'})
            .then(async () => { focusTask = source.id; await refresh(); $(`[data-task-reorder-handle="${source.id}"]`)?.focus({preventScroll: true}); })
            .catch(async error => { if (error.status === 409) { undo = []; try { await refresh(); } catch (_) { /* Show original conflict. */ } } message(error.message, true); })
            .finally(() => busy(false));
        }
        return;
      }
      if (!root.contains(document.activeElement) || document.activeElement.closest('input,textarea,select,[contenteditable="true"],dialog[open]')) return;
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'z') { event.preventDefault(); runUndo(); }
    });
  };
  const disposeIfDetached = () => {
    if (!root || root.isConnected) return;
    generation++; taskFormGeneration++;
    controller?.abort(); rangeController?.abort(); taskFormController?.abort(); rowObserver?.disconnect();
    clearTimeout(rangeTimer); cancelAnimationFrame(frame); cancelAnimationFrame(scrollFrame);
    document.removeEventListener('mmp:page-loaded', disposeIfDetached);
  };
  document.addEventListener('mmp:page-loaded', disposeIfDetached);
  initialize();
})();
