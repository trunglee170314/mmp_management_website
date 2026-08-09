document.addEventListener('click', event => {
  const trigger = event.target.closest('.popover-trigger');
  if (trigger) trigger.parentElement.classList.toggle('menu-open');
  else if (!event.target.closest('.sidebar-user')) document.querySelectorAll('.menu-open').forEach(el => el.classList.remove('menu-open'));

  document.querySelectorAll('details.inline-add[open]').forEach(details => {
    if (!details.contains(event.target)) details.removeAttribute('open');
  });

  const clickedFilter = event.target.closest('details.multi-filter');
  const clickedSummary = event.target.closest('summary');
  if (!clickedFilter) {
    document.querySelectorAll('details.multi-filter[open]').forEach(details => details.removeAttribute('open'));
  } else if (clickedSummary === clickedFilter.querySelector(':scope > summary')) {
    document.querySelectorAll('details.multi-filter[open]').forEach(details => {
      if (details !== clickedFilter) details.removeAttribute('open');
    });
  }
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const openFilters = [...document.querySelectorAll('details.multi-filter[open]')];
  if (!openFilters.length) return;
  openFilters.forEach(details => details.removeAttribute('open'));
  openFilters.at(-1).querySelector(':scope > summary')?.focus();
});

document.addEventListener('submit', event => {
  const form = event.target.closest('form[data-confirm]');
  if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
});

const initializeMessages = (root = document) => {
  root.querySelectorAll('.message:not([data-message-ready])').forEach(message => {
    message.dataset.messageReady = 'true';
    window.setTimeout(() => message.classList.add('fade'), 4200);
  });
};

initializeMessages();

const pageScrollKey = `mmp-page-scroll:${window.location.pathname}`;
const storedPageScroll = window.sessionStorage.getItem(pageScrollKey);
if (storedPageScroll !== null) {
  window.sessionStorage.removeItem(pageScrollKey);
  window.requestAnimationFrame(() => window.scrollTo(0, Number(storedPageScroll) || 0));
}

document.addEventListener('submit', event => {
  const form = event.target.closest('[data-preserve-page-scroll]');
  if (!form || event.defaultPrevented) return;
  window.sessionStorage.setItem(pageScrollKey, String(window.scrollY));
});

const createMeetingActionRow = (action, container) => {
  const row = document.createElement('div');
  const editable = container?.dataset.canEditActions === 'true' && !action.is_published;
  row.className = `action-row${editable ? ' editable-action-row' : ''}`;
  row.dataset.actionId = action.id;
  if (action.edit_url) row.dataset.editUrl = action.edit_url;
  if (action.delete_url) row.dataset.deleteUrl = action.delete_url;

  const check = document.createElement('span');
  check.className = `round-check${action.is_completed ? ' checked' : ''}`;
  check.textContent = action.is_completed ? '✓' : '';

  const content = document.createElement('span');
  content.className = 'action-item-content';
  content.textContent = action.content;

  const meta = document.createElement('small');
  meta.className = 'action-item-meta';
  meta.textContent = `${action.assignee}${action.due_label ? ` · Due ${action.due_label}` : ''}`;

  row.append(check, content, meta);
  if (editable) {
    const actions = document.createElement('div');
    actions.className = 'action-row-actions no-print';

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.dataset.actionEdit = '';
    edit.textContent = 'Edit';

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'text-danger';
    remove.dataset.actionDelete = '';
    remove.textContent = 'Delete';
    actions.append(edit, remove);

    const form = document.createElement('form');
    form.className = 'inline-action-edit no-print';
    form.dataset.actionEditForm = '';
    form.hidden = true;

    const contentInput = document.createElement('input');
    contentInput.name = 'content';
    contentInput.maxLength = 300;
    contentInput.required = true;
    contentInput.value = action.content;
    contentInput.defaultValue = action.content;
    contentInput.setAttribute('aria-label', 'Action item content');

    const assignee = document.createElement('select');
    assignee.name = 'assignee';
    assignee.setAttribute('aria-label', 'Action item assignee');
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Unassigned';
    assignee.append(blank);
    const sourceAssignees = container.closest('[data-entry-id]')?.querySelector('[data-action-item-form] select[name="action_assignee"]');
    [...(sourceAssignees?.options || [])].filter(option => option.value).forEach(option => assignee.append(option.cloneNode(true)));
    assignee.value = String(action.assignee_id || '');
    [...assignee.options].forEach(option => { option.defaultSelected = option.selected; });

    const dueDate = document.createElement('input');
    dueDate.type = 'date';
    dueDate.name = 'due_date';
    dueDate.value = action.due_date || '';
    dueDate.defaultValue = action.due_date || '';
    dueDate.setAttribute('aria-label', 'Action item due date');

    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'button primary mini';
    save.textContent = 'Save';

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'button secondary mini';
    cancel.dataset.actionCancel = '';
    cancel.textContent = 'Cancel';

    const status = document.createElement('span');
    status.className = 'inline-action-status';
    status.dataset.actionEditStatus = '';
    form.append(contentInput, assignee, dueDate, save, cancel, status);
    row.append(actions, form);
  }
  return row;
};

const renderMeetingActions = (container, actions) => {
  container.replaceChildren();
  if (!actions.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No new action items yet.';
    container.append(empty);
    return;
  }
  actions.forEach(action => container.append(createMeetingActionRow(action, container)));
};

const initializeProgressAutosave = (root = document) => {
  root.querySelectorAll('[data-progress-autosave]:not([data-progress-ready])').forEach(form => {
  form.dataset.progressReady = 'true';
  const textarea = form.querySelector('textarea');
  const status = form.closest('.progress-section').querySelector('[data-autosave-status]');
  const card = form.closest('[data-entry-id]');
  form.dataset.progressDirty = 'false';
  let timer;
  let revision = 0;
  let savedRevision = 0;
  let requestInFlight = false;
  let consecutiveFailures = 0;

  const setStatus = (message, state) => {
    status.textContent = message;
    status.className = `autosave-status ${state}`;
  };

  const save = async () => {
    window.clearTimeout(timer);
    timer = undefined;
    if (requestInFlight || revision === savedRevision) return;
    requestInFlight = true;
    const sendingRevision = revision;
    const sendingValue = textarea.value;
    setStatus('Saving…', 'is-saving');
    try {
      const response = await fetch(form.dataset.saveUrl, {
        method: 'POST',
        headers: {'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
        body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to save.');
      savedRevision = sendingRevision;
      consecutiveFailures = 0;
      card.dataset.liveVersion = data.updated_at;
      broadcastMeetingChange({
        type: 'progress',
        entryId: Number(card.dataset.entryId),
        weeklyProgress: sendingValue,
        updatedAt: data.updated_at,
        updatedBy: data.updated_by,
      });
      if (revision === savedRevision) {
        form.dataset.progressDirty = 'false';
        setStatus('Saved just now', 'is-saved');
      }
    } catch (error) {
      consecutiveFailures += 1;
      const retryDelay = [2000, 5000, 10000, 20000, 30000][Math.min(consecutiveFailures - 1, 4)];
      setStatus(`${error.message || 'Not saved'} — retrying in ${Math.round(retryDelay / 1000)}s`, 'is-error');
      timer = window.setTimeout(save, retryDelay);
    } finally {
      requestInFlight = false;
      if (revision !== savedRevision && !timer) timer = window.setTimeout(save, 900);
    }
  };

  textarea.addEventListener('input', () => {
    revision += 1;
    form.dataset.progressDirty = 'true';
    consecutiveFailures = 0;
    setStatus('Waiting to save…', 'is-waiting');
    window.clearTimeout(timer);
    timer = undefined;
    timer = window.setTimeout(save, 900);
  });
  textarea.addEventListener('blur', save);
  });
};

initializeProgressAutosave();

const initializeMeetingActionForms = (root = document) => {
  root.querySelectorAll('[data-action-item-form]:not([data-action-form-ready])').forEach(form => {
  form.dataset.actionFormReady = 'true';
  const status = form.querySelector('[data-action-status]');
  const card = form.closest('[data-entry-id]');
  const list = card.querySelector('[data-live-actions]');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('button[type=submit]');
    submit.disabled = true;
    status.textContent = 'Adding…';
    status.className = 'action-save-status is-saving';
    try {
      const response = await fetch(form.dataset.addUrl, {
        method: 'POST',
        headers: {'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
        body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to add Action Item.');
      list.querySelector('.muted')?.remove();
      list.append(createMeetingActionRow(data.action, list));
      card.dataset.liveVersion = data.updated_at;
      broadcastMeetingChange({
        type: 'action-upsert',
        entryId: Number(card.dataset.entryId),
        action: data.action,
        updatedAt: data.updated_at,
        updatedBy: data.updated_by,
      });
      form.querySelector('[name=new_action_item]').value = '';
      form.querySelector('[name=action_due_date]').value = '';
      status.textContent = 'Added and saved.';
      status.className = 'action-save-status is-saved';
      form.querySelector('[name=new_action_item]').focus();
    } catch (error) {
      status.textContent = error.message || 'Not saved — retry.';
      status.className = 'action-save-status is-error';
    } finally {
      submit.disabled = false;
    }
  });
  });
};

initializeMeetingActionForms();

const actionCsrfToken = row => row.closest('[data-entry-id]')?.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';

const showEmptyActionList = list => {
  if (list.querySelector('[data-action-id]')) return;
  const empty = document.createElement('p');
  empty.className = 'muted';
  empty.textContent = 'No new action items yet.';
  list.append(empty);
};

const insertActionInCreatedOrder = (list, row) => {
  const rows = [...list.querySelectorAll('[data-action-id]')];
  const nextRow = rows.find(candidate => {
    if (candidate.dataset.createdAt !== row.dataset.createdAt) {
      return candidate.dataset.createdAt > row.dataset.createdAt;
    }
    return Number(candidate.dataset.actionId) > Number(row.dataset.actionId);
  });
  list.insertBefore(row, nextRow || null);
};

const updateCompletedActionCount = details => {
  const count = details.querySelectorAll('[data-completed-action-list] > [data-action-id]').length;
  details.querySelector('[data-completed-count]').textContent = count;
  details.hidden = count === 0;
  if (!count) details.open = false;
};

const createPreviousActionRow = (action, card, completed) => {
  const row = document.createElement('div');
  row.className = `action-row ${completed ? 'recently-completed-row' : 'previous-action-row'}`;
  row.dataset.actionId = action.id;
  row.dataset.createdAt = action.created_at;

  if (action.can_toggle) {
    const form = document.createElement('form');
    form.method = 'post';
    form.action = action.toggle_url;
    form.dataset.actionToggle = '';
    const csrf = document.createElement('input');
    csrf.type = 'hidden';
    csrf.name = 'csrfmiddlewaretoken';
    csrf.value = currentMeetingRoot()?.dataset.csrfToken
      || card.querySelector('[name="csrfmiddlewaretoken"]')?.value
      || '';
    const next = document.createElement('input');
    next.type = 'hidden';
    next.name = 'next';
    next.value = `${window.location.pathname}#${card.id}`;
    const button = document.createElement('button');
    button.type = 'submit';
    button.className = `round-check no-print${completed ? ' checked' : ''}`;
    button.textContent = completed ? '✓' : '';
    button.setAttribute('aria-label', completed ? 'Reopen action item' : 'Complete action item');
    button.title = completed ? 'Reopen action item' : 'Mark as completed';
    form.append(csrf, next, button);
    row.append(form);
  } else {
    const check = document.createElement('span');
    check.className = `round-check${completed ? ' checked' : ''}`;
    check.textContent = completed ? '✓' : '';
    check.setAttribute('aria-hidden', 'true');
    row.append(check);
  }

  const content = document.createElement('span');
  content.className = 'action-item-content';
  content.textContent = action.content;
  const meta = document.createElement('small');
  meta.className = 'action-item-meta';
  meta.textContent = completed
    ? `${action.completed_by || 'System'} · ${action.completed_label}`
    : `${action.assignee}${action.due_label ? ` · Due ${action.due_label}` : ''}`;
  row.append(content, meta);
  return row;
};

const renderPreviousActions = (card, openActions, completedActions) => {
  const openList = card.querySelector('[data-open-actions]');
  const completedDetails = card.querySelector('[data-completed-actions]');
  const completedList = completedDetails?.querySelector('[data-completed-action-list]');
  if (!openList || !completedDetails || !completedList) return;

  openList.replaceChildren();
  if (openActions.length) {
    openActions.forEach(action => openList.append(createPreviousActionRow(action, card, false)));
  } else {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.dataset.openActionsEmpty = '';
    empty.textContent = 'No open action items.';
    openList.append(empty);
  }
  completedList.replaceChildren(
    ...completedActions.map(action => createPreviousActionRow(action, card, true)),
  );
  updateCompletedActionCount(completedDetails);
};

const applyPreviousActionToggle = (card, action) => {
  const row = card.querySelector(`[data-open-actions] [data-action-id="${action.id}"], [data-completed-action-list] [data-action-id="${action.id}"]`);
  if (!row) return false;
  const openList = card.querySelector('[data-open-actions]');
  const completedDetails = card.querySelector('[data-completed-actions]');
  const completedList = completedDetails.querySelector('[data-completed-action-list]');
  const button = row.querySelector('.round-check');
  const meta = row.querySelector('.action-item-meta');
  row.dataset.createdAt = action.created_at;
  row.classList.toggle('recently-completed-row', action.is_completed);
  row.classList.toggle('previous-action-row', !action.is_completed);
  button.classList.toggle('checked', action.is_completed);
  button.textContent = action.is_completed ? '✓' : '';
  button.setAttribute('aria-label', action.is_completed ? 'Reopen action item' : 'Complete action item');
  button.title = action.is_completed ? 'Reopen action item' : 'Mark as completed';
  row.querySelector('.action-item-content').textContent = action.content;

  if (action.is_completed) {
    meta.textContent = `${action.completed_by || 'System'} · ${action.completed_label}`;
    completedDetails.hidden = false;
    completedList.prepend(row);
    if (!openList.querySelector('[data-action-id]')) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.dataset.openActionsEmpty = '';
      empty.textContent = 'No open action items.';
      openList.append(empty);
    }
  } else {
    meta.textContent = `${action.assignee}${action.due_label ? ` · Due ${action.due_label}` : ''}`;
    openList.querySelector('[data-open-actions-empty]')?.remove();
    insertActionInCreatedOrder(openList, row);
  }
  updateCompletedActionCount(completedDetails);
  return true;
};

const addGlobalActionEmptyState = list => {
  if (!list || list.querySelector('[data-action-row]') || list.querySelector('[data-action-empty]')) return;
  const message = list.dataset.emptyMessage || 'No action items.';
  if (list.tagName === 'TBODY') {
    const row = document.createElement('tr');
    row.dataset.actionEmpty = '';
    const cell = document.createElement('td');
    cell.colSpan = 7;
    cell.className = 'empty';
    cell.textContent = message;
    row.append(cell);
    list.append(row);
    return;
  }
  const empty = document.createElement('div');
  empty.dataset.actionEmpty = '';
  empty.className = 'empty';
  empty.textContent = message;
  list.append(empty);
};

const updateGlobalActionRow = (row, action) => {
  const button = row.querySelector('.round-check');
  row.classList.toggle('completed-row', action.is_completed);
  button.classList.toggle('checked', action.is_completed);
  button.textContent = action.is_completed ? '✓' : '';
  button.setAttribute('aria-label', action.is_completed ? 'Reopen action item' : 'Complete action item');
  button.title = action.is_completed ? 'Reopen' : 'Mark as completed';

  const completion = row.querySelector('[data-action-completion-meta]');
  if (completion) {
    completion.hidden = !action.is_completed;
    if (completion.dataset.inlineCompletion === 'true') {
      completion.textContent = action.is_completed ? ` · Completed ${action.completed_date}` : '';
    } else {
      completion.textContent = action.is_completed
        ? `Completed by ${action.completed_by || 'System'} · ${action.completed_display}`
        : '';
    }
  }

  const status = row.querySelector('[data-action-status]');
  if (status) {
    status.textContent = action.is_completed ? 'Completed' : 'Open';
    status.classList.toggle('done', action.is_completed);
    status.classList.toggle('todo', !action.is_completed);
  }
};

document.addEventListener('click', async event => {
  const clickedButton = event.target.closest('[data-global-action-toggle] .round-check');
  if (!clickedButton) return;
  event.preventDefault();
  const form = clickedButton.closest('[data-global-action-toggle]');
  const row = form.closest('[data-action-row]');
  const list = row?.closest('[data-action-list]');
  const button = clickedButton;
  button.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: 'POST',
      headers: {
        'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value,
        'Accept': 'application/json',
      },
      body: new FormData(form),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to update Action Item.');
    updateGlobalActionRow(row, data.action);

    if (data.action.is_completed && row.dataset.removeOnComplete === 'true') {
      row.classList.add('is-removing');
      window.setTimeout(() => {
        row.remove();
        addGlobalActionEmptyState(list);
      }, 170);
      return;
    }
  } catch (error) {
    window.alert(error.message || 'Unable to update Action Item.');
  } finally {
    button.disabled = false;
  }
});

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-action-toggle]');
  if (!form) return;
  event.preventDefault();
  const row = form.closest('[data-action-id]');
  const section = row.closest('.minute-section');
  const button = form.querySelector('.round-check');
  button.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: 'POST',
      headers: {
        'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value,
        'Accept': 'application/json',
      },
      body: new FormData(form),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to update Action Item.');

    const action = data.action;
    const card = section.closest('[data-entry-id]');
    applyPreviousActionToggle(card, action);
    card.dataset.liveVersion = data.updated_at;
    broadcastMeetingChange({
      type: 'previous-action-toggle',
      entryId: Number(card.dataset.entryId),
      action,
      updatedAt: data.updated_at,
      updatedBy: data.updated_by,
    });
  } catch (error) {
    window.alert(error.message || 'Unable to update Action Item.');
  } finally {
    button.disabled = false;
  }
});

document.addEventListener('click', async event => {
  const editButton = event.target.closest('[data-action-edit]');
  if (editButton) {
    const row = editButton.closest('[data-action-id]');
    row.classList.add('is-editing');
    const form = row.querySelector('[data-action-edit-form]');
    form.hidden = false;
    form.querySelector('[name="content"]').focus();
    return;
  }

  const cancelButton = event.target.closest('[data-action-cancel]');
  if (cancelButton) {
    const row = cancelButton.closest('[data-action-id]');
    row.classList.remove('is-editing');
    const form = row.querySelector('[data-action-edit-form]');
    form.reset();
    form.hidden = true;
    row.querySelector('[data-action-edit-status]').textContent = '';
    return;
  }

  const deleteButton = event.target.closest('[data-action-delete]');
  if (!deleteButton) return;
  const row = deleteButton.closest('[data-action-id]');
  if (!window.confirm('Delete this Action Item?')) return;
  deleteButton.disabled = true;
  try {
    const response = await fetch(row.dataset.deleteUrl, {
      method: 'POST',
      headers: {'X-CSRFToken': actionCsrfToken(row), 'Accept': 'application/json'},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to delete Action Item.');
    const list = row.closest('[data-live-actions]');
    const card = row.closest('[data-entry-id]');
    row.remove();
    showEmptyActionList(list);
    card.dataset.liveVersion = data.updated_at;
    broadcastMeetingChange({
      type: 'action-delete',
      entryId: Number(card.dataset.entryId),
      actionId: data.action_id,
      updatedAt: data.updated_at,
      updatedBy: data.updated_by,
    });
  } catch (error) {
    window.alert(error.message || 'Unable to delete Action Item.');
    deleteButton.disabled = false;
  }
});

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-action-edit-form]');
  if (!form) return;
  event.preventDefault();
  const row = form.closest('[data-action-id]');
  const status = form.querySelector('[data-action-edit-status]');
  const buttons = [...form.querySelectorAll('button')];
  buttons.forEach(button => { button.disabled = true; });
  status.textContent = 'Saving…';
  status.className = 'inline-action-status is-saving';
  try {
    const response = await fetch(row.dataset.editUrl, {
      method: 'POST',
      headers: {'X-CSRFToken': actionCsrfToken(row), 'Accept': 'application/json'},
      body: new FormData(form),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to save Action Item.');
    const list = row.closest('[data-live-actions]');
    const card = row.closest('[data-entry-id]');
    row.replaceWith(createMeetingActionRow(data.action, list));
    card.dataset.liveVersion = data.updated_at;
    broadcastMeetingChange({
      type: 'action-upsert',
      entryId: Number(card.dataset.entryId),
      action: data.action,
      updatedAt: data.updated_at,
      updatedBy: data.updated_by,
    });
  } catch (error) {
    status.textContent = error.message || 'Not saved — retry.';
    status.className = 'inline-action-status is-error';
    buttons.forEach(button => { button.disabled = false; });
  }
});

const currentMeetingRoot = () => document.querySelector('.meeting-live-root');

const meetingBroadcastChannel = typeof window.BroadcastChannel === 'function'
  ? new BroadcastChannel('mmp-meeting-live-v1')
  : null;

const highlightMeetingCard = card => {
  card.classList.remove('live-updated');
  window.requestAnimationFrame(() => card.classList.add('live-updated'));
  window.setTimeout(() => card.classList.remove('live-updated'), 1800);
};

const broadcastMeetingChange = payload => {
  const meetingRoot = currentMeetingRoot();
  if (!meetingRoot) return;
  meetingBroadcastChannel?.postMessage({
    ...payload,
    meetingId: meetingRoot.dataset.meetingId,
  });
  scheduleMeetingPoll(3000);
};

const updateMeetingReviewSummary = (meetingRoot, reviewed, total) => {
  const count = meetingRoot.querySelector('[data-reviewed-count]');
  const progress = meetingRoot.querySelector('[data-reviewed-progress]');
  const progressTrack = progress?.closest('[role="progressbar"]');
  if (count) count.textContent = `${reviewed} / ${total} tasks reviewed`;
  if (progress) progress.style.width = `${total ? Math.round(reviewed / total * 100) : 0}%`;
  if (progressTrack) {
    progressTrack.setAttribute('aria-valuenow', String(reviewed));
    progressTrack.setAttribute('aria-valuemax', String(total));
  }
};

const applyLiveReview = (meetingRoot, card, entry) => {
  const navLink = meetingRoot.querySelector(`[data-nav-entry-id="${entry.id}"]`);
  if (navLink) navLink.className = entry.review_state;
  card.querySelectorAll('[data-meeting-review] [name="review_state"]').forEach(button => {
    const active = button.value === entry.review_state;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  const result = card.querySelector('[data-live-review-result]');
  if (result) result.textContent = entry.review_label;
};

const applyBroadcastMeetingChange = message => {
  const meetingRoot = currentMeetingRoot();
  if (!meetingRoot || String(message.meetingId) !== meetingRoot.dataset.meetingId) return;
  if (message.type === 'order') {
    applyMeetingOrder(message.entries);
    syncMeetingOrderMode(meetingRoot, message.mode);
    return;
  }
  const card = meetingRoot.querySelector(`[data-entry-id="${message.entryId}"]`);
  if (!card) return;

  if (message.type === 'review') {
    applyLiveReview(meetingRoot, card, message);
    updateMeetingReviewSummary(meetingRoot, message.reviewed, message.total);
  } else if (message.type === 'previous-action-toggle') {
    if (!applyPreviousActionToggle(card, message.action)) return;
  } else if (message.type === 'progress') {
    const progressForm = card.querySelector('[data-progress-autosave]');
    if (progressForm?.dataset.progressDirty === 'true') return;
    const textarea = progressForm?.querySelector('textarea');
    const progress = card.querySelector('[data-live-progress]');
    if (textarea) {
      textarea.value = message.weeklyProgress || '';
      textarea.defaultValue = textarea.value;
      const status = card.querySelector('[data-autosave-status]');
      if (status) {
        status.textContent = 'Updated live';
        status.className = 'autosave-status is-saved';
      }
    }
    if (progress) progress.textContent = message.weeklyProgress || 'No progress update recorded.';
  } else if (message.type === 'action-delete' || message.type === 'action-upsert') {
    const actions = card.querySelector('[data-live-actions]');
    if (!actions || actions.querySelector('[data-action-edit-form]:not([hidden])')) return;
    if (message.type === 'action-delete') {
      actions.querySelector(`[data-action-id="${message.actionId}"]`)?.remove();
      showEmptyActionList(actions);
    } else if (message.type === 'action-upsert') {
      const row = createMeetingActionRow(message.action, actions);
      const existing = actions.querySelector(`[data-action-id="${message.action.id}"]`);
      if (existing) existing.replaceWith(row);
      else {
        actions.querySelector('.muted')?.remove();
        actions.append(row);
      }
    }
  }

  const meta = card.querySelector('[data-live-meta]');
  if (meta) meta.textContent = `Updated by ${message.updatedBy} · just now`;
  card.dataset.liveVersion = message.updatedAt;
  highlightMeetingCard(card);
};

meetingBroadcastChannel?.addEventListener('message', event => {
  applyBroadcastMeetingChange(event.data || {});
  scheduleMeetingPoll(3000);
});

const visibleMeetingCard = () => {
  const meetingRoot = currentMeetingRoot();
  if (!meetingRoot) return null;
  return [...meetingRoot.querySelectorAll('[data-entry-id]')].find(card => card.getBoundingClientRect().bottom > 80) || null;
};

const applyMeetingOrder = (entries, anchor = visibleMeetingCard()) => {
  const meetingRoot = currentMeetingRoot();
  if (!meetingRoot) return;
  const beforeTop = anchor?.getBoundingClientRect().top;
  const taskList = meetingRoot.querySelector('.meeting-tasks');
  const nav = meetingRoot.querySelector('.meeting-nav nav');
  entries.forEach(entry => {
    const card = meetingRoot.querySelector(`[data-entry-id="${entry.id}"]`);
    const navLink = meetingRoot.querySelector(`[data-nav-entry-id="${entry.id}"]`);
    if (card) taskList.append(card);
    if (navLink) {
      navLink.querySelector(':scope > span').textContent = entry.position;
      nav.append(navLink);
    }
  });
  if (anchor && beforeTop !== undefined) {
    const afterTop = anchor.getBoundingClientRect().top;
    window.scrollBy(0, afterTop - beforeTop);
  }
};

const syncMeetingOrderMode = (meetingRoot, mode) => {
  if (!meetingRoot || !mode) return;
  const orderSelect = meetingRoot.querySelector('[data-order-select]');
  if (orderSelect && [...orderSelect.options].some(option => option.value === mode)) {
    orderSelect.value = mode;
  }
  const url = new URL(window.location.href);
  url.searchParams.set('order', mode);
  window.history.replaceState(window.history.state, '', url);
};

document.addEventListener('submit', async event => {
  const reviewForm = event.target.closest('[data-meeting-review]');
  if (!reviewForm) return;
  event.preventDefault();
  const meetingRoot = currentMeetingRoot();
  const clickedButton = event.submitter;
  if (!clickedButton?.value) return;
  const buttons = [...reviewForm.querySelectorAll('button[type="submit"]')];
  const status = reviewForm.querySelector('[data-review-save-status]');
  const card = reviewForm.closest('[data-entry-id]');
  buttons.forEach(button => { button.disabled = true; });
  status.textContent = 'Saving…';
  status.className = 'review-save-status is-saving';
  try {
    const formData = new FormData(reviewForm);
    formData.set(clickedButton.name, clickedButton.value);
    const response = await fetch(reviewForm.action, {
      method: 'POST',
      headers: {
        'X-CSRFToken': reviewForm.querySelector('[name="csrfmiddlewaretoken"]').value,
        'Accept': 'application/json',
      },
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to save review.');
    card.dataset.liveVersion = data.updated_at;
    const navLink = meetingRoot.querySelector(`[data-nav-entry-id="${data.entry_id}"]`);
    if (navLink) navLink.className = data.review_state;
    reviewForm.querySelectorAll('[name="review_state"]').forEach(button => {
      const active = button.value === data.review_state;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    updateMeetingReviewSummary(meetingRoot, data.reviewed, data.total);
    broadcastMeetingChange({
      type: 'review',
      entryId: data.entry_id,
      review_state: data.review_state,
      review_label: data.review_label,
      reviewed: data.reviewed,
      total: data.total,
      updatedAt: data.updated_at,
      updatedBy: data.updated_by,
    });
    status.textContent = 'Saved';
    status.className = 'review-save-status is-saved';
    if (data.next_entry_id !== data.entry_id) {
      const nextCard = meetingRoot.querySelector(`[data-entry-id="${data.next_entry_id}"]`);
      window.setTimeout(() => nextCard?.scrollIntoView({behavior: 'smooth', block: 'start'}), 120);
    }
  } catch (error) {
    status.textContent = error.message || 'Not saved — retry.';
    status.className = 'review-save-status is-error';
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
});

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-meeting-order], [data-meeting-move]');
  if (!form) return;
  event.preventDefault();
  const meetingRoot = currentMeetingRoot();
  const anchor = form.closest('[data-entry-id]') || visibleMeetingCard();
  const controls = [...form.querySelectorAll('button, select')];
  // Disabled fields are omitted from FormData, so capture the selected order first.
  const formData = new FormData(form);
  controls.forEach(control => { control.disabled = true; });
  try {
    const response = await fetch(form.action, {
      method: 'POST',
      headers: {
        'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value,
        'Accept': 'application/json',
      },
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to reorder tasks.');
    applyMeetingOrder(data.entries, anchor);
    broadcastMeetingChange({
      type: 'order',
      entries: data.entries,
      mode: data.mode,
      updatedAt: data.updated_at,
      updatedBy: data.updated_by,
    });
    syncMeetingOrderMode(meetingRoot, data.mode);
  } catch (error) {
    window.alert(error.message || 'Unable to reorder tasks.');
  } finally {
    controls.forEach(control => { control.disabled = false; });
  }
});

const meetingScrollKey = `mmp-meeting-scroll:${window.location.pathname}`;
const storedMeetingScroll = window.sessionStorage.getItem(meetingScrollKey);
if (storedMeetingScroll !== null) {
  window.sessionStorage.removeItem(meetingScrollKey);
  window.requestAnimationFrame(() => window.scrollTo(0, Number(storedMeetingScroll) || 0));
}
document.querySelectorAll('form[data-preserve-scroll]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!event.defaultPrevented) window.sessionStorage.setItem(meetingScrollKey, String(window.scrollY));
  });
});

let meetingPolling = false;
let meetingPollTimer;
let unchangedMeetingPolls = 0;
let lastMeetingPollUrl = '';

const scheduleMeetingPoll = delay => {
  window.clearTimeout(meetingPollTimer);
  if (document.hidden) return;
  const jitteredDelay = delay
    ? Math.round(delay * (0.9 + Math.random() * 0.2))
    : 0;
  meetingPollTimer = window.setTimeout(pollMeetingUpdates, jitteredDelay);
};

const nextMeetingPollDelay = changed => {
  if (changed) {
    unchangedMeetingPolls = 0;
    return 3000;
  }
  unchangedMeetingPolls += 1;
  return [5000, 8000, 12000][Math.min(unchangedMeetingPolls - 1, 2)];
};

const pollMeetingUpdates = async () => {
    const meetingLiveRoot = document.querySelector('[data-live-updates-url][data-live-enabled="true"]');
    if (!meetingLiveRoot || document.hidden) return;
    if (meetingPolling) return;
    if (lastMeetingPollUrl !== meetingLiveRoot.dataset.liveUpdatesUrl) {
      lastMeetingPollUrl = meetingLiveRoot.dataset.liveUpdatesUrl;
      unchangedMeetingPolls = 0;
    }
    meetingPolling = true;
    let nextDelay = 8000;
    try {
      const liveUrl = new URL(meetingLiveRoot.dataset.liveUpdatesUrl, window.location.origin);
      if (meetingLiveRoot.dataset.liveVersion) {
        liveUrl.searchParams.set('since', meetingLiveRoot.dataset.liveVersion);
      }
      const response = await fetch(liveUrl, {
        cache: 'no-store',
        headers: {'Accept': 'application/json'},
      });
      if (!response.ok) return;
      const data = await response.json();
      const currentVersion = Date.parse(meetingLiveRoot.dataset.liveVersion || '');
      const responseVersion = Date.parse(data.updated_at || '');
      if (Number.isFinite(currentVersion) && Number.isFinite(responseVersion) && responseVersion < currentVersion) {
        nextDelay = nextMeetingPollDelay(false);
        return;
      }
      if (!data.changed) {
        meetingLiveRoot.dataset.liveVersion = data.updated_at;
        nextDelay = nextMeetingPollDelay(false);
        return;
      }
      if (data.meeting_status !== 'draft') {
        window.location.reload();
        return;
      }
      updateMeetingReviewSummary(meetingLiveRoot, data.reviewed, data.total);
      const currentOrder = [...meetingLiveRoot.querySelectorAll('.meeting-tasks > [data-entry-id]')]
        .map(card => Number(card.dataset.entryId));
      const nextOrder = data.order.map(entry => entry.id);
      if (currentOrder.some((id, index) => id !== nextOrder[index])) {
        applyMeetingOrder(data.order);
      }
      syncMeetingOrderMode(meetingLiveRoot, data.order_mode);
      let fullyApplied = true;
      data.entries.forEach(entry => {
        const card = meetingLiveRoot.querySelector(`[data-entry-id="${entry.id}"]`);
        if (!card || card.dataset.liveVersion === entry.updated_at) return;
        const progressForm = card.querySelector('[data-progress-autosave]');
        const progressIsDirty = progressForm?.dataset.progressDirty === 'true';
        const progressTextarea = progressForm?.querySelector('textarea');
        const progress = card.querySelector('[data-live-progress]');
        const actions = card.querySelector('[data-live-actions]');
        const actionEditIsOpen = Boolean(actions?.querySelector('[data-action-edit-form]:not([hidden])'));
        const meta = card.querySelector('[data-live-meta]');
        applyLiveReview(meetingLiveRoot, card, entry);
        renderPreviousActions(card, entry.open_actions, entry.recent_completed_actions);
        if (progress) progress.textContent = entry.weekly_progress || 'No progress update recorded.';
        if (progressTextarea && !progressIsDirty) {
          progressTextarea.value = entry.weekly_progress || '';
          progressTextarea.defaultValue = progressTextarea.value;
          const autosaveStatus = card.querySelector('[data-autosave-status]');
          if (autosaveStatus) {
            autosaveStatus.textContent = 'Updated live';
            autosaveStatus.className = 'autosave-status is-saved';
          }
        }
        if (actions && !actionEditIsOpen) renderMeetingActions(actions, entry.actions);
        if (meta) meta.textContent = `Updated by ${entry.updated_by} · just now`;
        if (!progressIsDirty && !actionEditIsOpen) {
          card.dataset.liveVersion = entry.updated_at;
          highlightMeetingCard(card);
        } else fullyApplied = false;
      });
      if (fullyApplied) meetingLiveRoot.dataset.liveVersion = data.updated_at;
      nextDelay = nextMeetingPollDelay(true);
    } catch (_) {
      // The next local polling cycle will retry automatically.
    } finally {
      meetingPolling = false;
      if (document.querySelector('[data-live-updates-url][data-live-enabled="true"]')) {
        scheduleMeetingPoll(nextDelay);
      }
    }
};
scheduleMeetingPoll(3000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) scheduleMeetingPoll(0);
});

const registerForm = document.querySelector('.register-form[data-password-check-url]');
if (registerForm) {
  const password = registerForm.querySelector('#id_password1');
  const watchedFields = ['#id_password1', '#id_username', '#id_email', '#id_display_name']
    .map(selector => registerForm.querySelector(selector));
  let passwordTimer;

  const updateRules = results => {
    registerForm.querySelectorAll('[data-rule]').forEach(item => {
      const passed = Boolean(results[item.dataset.rule]);
      item.classList.toggle('valid', passed);
      item.querySelector('i').textContent = passed ? '✓' : '×';
    });
  };

  const checkPassword = () => {
    window.clearTimeout(passwordTimer);
    passwordTimer = window.setTimeout(async () => {
      if (!password.value) {
        updateRules({});
        return;
      }
      try {
        const response = await fetch(registerForm.dataset.passwordCheckUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': registerForm.querySelector('[name=csrfmiddlewaretoken]').value,
          },
          body: JSON.stringify({
            password: password.value,
            username: registerForm.querySelector('#id_username').value,
            email: registerForm.querySelector('#id_email').value,
            display_name: registerForm.querySelector('#id_display_name').value,
          }),
        });
        if (response.ok) updateRules(await response.json());
      } catch (_) {
        updateRules({});
      }
    }, 180);
  };

  watchedFields.forEach(field => field.addEventListener('input', checkPassword));
}

const initializeBoardPickers = (root = document) => {
  root.querySelectorAll('[data-multi-picker]:not([data-multi-picker-ready]), [data-board-picker]:not([data-multi-picker-ready])').forEach(picker => {
  picker.dataset.multiPickerReady = 'true';
  const toggle = picker.querySelector('.board-picker-toggle');
  const value = picker.querySelector('.board-picker-value');
  const options = picker.querySelector('.board-picker-options');
  const checkboxes = [...picker.querySelectorAll('input[type="checkbox"]')];
  const search = picker.querySelector('[data-multi-search], [data-board-search]');
  const noResults = picker.querySelector('.board-picker-no-results');
  const optionRows = checkboxes.map(checkbox => checkbox.closest('label')?.parentElement).filter(Boolean);
  const emptyLabel = picker.dataset.emptyLabel || 'Select boards';
  const countNoun = picker.dataset.countNoun || 'boards';

  const resetSearch = () => {
    if (!search) return;
    search.value = '';
    optionRows.forEach(row => { row.hidden = false; });
    if (noResults) noResults.hidden = true;
  };

  const close = () => {
    picker.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
    options.hidden = true;
    resetSearch();
  };
  picker.closeMultiPicker = close;
  picker.closeBoardPicker = close;

  const updateValue = () => {
    const selected = checkboxes.filter(checkbox => checkbox.checked);
    const names = selected.map(checkbox => checkbox.closest('label').textContent.trim());
    value.textContent = names.length === 0
      ? emptyLabel
      : names.length <= 2 ? names.join(', ') : `${names.length} ${countNoun} selected`;
    toggle.title = names.join(', ');
  };

  toggle.addEventListener('click', () => {
    const opening = options.hidden;
    if (opening) {
      picker.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
      options.hidden = false;
      window.requestAnimationFrame(() => search?.focus());
    } else {
      close();
    }
  });

  checkboxes.forEach(checkbox => checkbox.addEventListener('change', updateValue));
  search?.addEventListener('input', () => {
    const terms = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let visibleCount = 0;
    optionRows.forEach(row => {
      const text = row.textContent.toLocaleLowerCase();
      const matches = terms.every(term => text.includes(term));
      row.hidden = !matches;
      if (matches) visibleCount += 1;
    });
    if (noResults) noResults.hidden = visibleCount !== 0;
  });
  search?.addEventListener('keydown', event => {
    if (event.key === 'Enter') event.preventDefault();
  });
  picker.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      close();
      toggle.focus();
    }
  });
  updateValue();
  });
};

initializeBoardPickers();

const initializeSingleTaskPickers = (root = document) => {
  root.querySelectorAll('[data-single-task-picker]:not([data-single-task-picker-ready])').forEach(picker => {
    picker.dataset.singleTaskPickerReady = 'true';
    const toggle = picker.querySelector('.board-picker-toggle');
    const value = picker.querySelector('.board-picker-value');
    const options = picker.querySelector('.board-picker-options');
    const search = picker.querySelector('[data-single-task-search]');
    const optionList = picker.querySelector('[data-single-task-options]');
    const noResults = picker.querySelector('.board-picker-no-results');
    const select = picker.querySelector('select');
    if (!toggle || !value || !options || !optionList || !select) return;

    const close = () => {
      picker.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      options.hidden = true;
      if (search) search.value = '';
      optionList.querySelectorAll('.single-task-option').forEach(button => { button.hidden = false; });
      if (noResults) noResults.hidden = true;
    };
    picker.closeSingleTaskPicker = close;

    const updateValue = () => {
      const selected = select.options[select.selectedIndex];
      value.textContent = selected?.textContent.trim() || picker.dataset.emptyLabel || 'No parent task';
      toggle.title = value.textContent;
      optionList.querySelectorAll('.single-task-option').forEach(button => {
        button.classList.toggle('is-selected', button.dataset.value === select.value);
      });
    };

    [...select.options].forEach(option => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'single-task-option';
      button.dataset.value = option.value;
      button.textContent = option.textContent.trim();
      button.addEventListener('click', () => {
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles: true}));
        updateValue();
        close();
        toggle.focus();
      });
      optionList.append(button);
    });

    toggle.addEventListener('click', () => {
      const opening = options.hidden;
      if (opening) {
        picker.classList.add('is-open');
        toggle.setAttribute('aria-expanded', 'true');
        options.hidden = false;
        window.requestAnimationFrame(() => search?.focus());
      } else {
        close();
      }
    });

    search?.addEventListener('input', () => {
      const terms = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      let visibleCount = 0;
      optionList.querySelectorAll('.single-task-option').forEach(button => {
        const text = button.textContent.toLocaleLowerCase();
        const matches = terms.every(term => text.includes(term));
        button.hidden = !matches;
        if (matches) visibleCount += 1;
      });
      if (noResults) noResults.hidden = visibleCount !== 0;
    });
    search?.addEventListener('keydown', event => {
      if (event.key === 'Enter') event.preventDefault();
    });
    picker.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        close();
        toggle.focus();
      }
    });
    updateValue();
  });
};

initializeSingleTaskPickers();

const initializeFilterForms = (root = document) => {
  root.querySelectorAll('[data-filter-form]:not([data-filter-ready])').forEach(form => {
    form.dataset.filterReady = 'true';
    const submit = form.querySelector('[data-filter-submit]');
    if (!submit) return;
    const initiallyFiltered = form.dataset.filterActive === 'true';
    const trackedControls = () => [...form.querySelectorAll(
      'input[name="q"], details.multi-filter input[type="checkbox"], select[name="page_size"]'
    )];
    const snapshot = () => trackedControls().map(control => {
      if (control.type === 'checkbox') return `${control.name}:${control.value}:${control.checked}`;
      return `${control.name}:${control.value}`;
    }).join('|');
    const initialSnapshot = snapshot();

    const renderState = () => {
      const dirty = snapshot() !== initialSnapshot;
      const filtered = initiallyFiltered && !dirty;
      submit.textContent = filtered ? 'Filtered' : 'Apply';
      submit.classList.toggle('is-filtered', filtered);
      submit.setAttribute('aria-pressed', filtered ? 'true' : 'false');
      submit.setAttribute('aria-label', filtered ? 'Clear filters' : 'Apply filters');
      submit.title = filtered ? 'Clear filters' : '';
      return filtered;
    };

    form.addEventListener('input', renderState);
    form.addEventListener('change', renderState);
    form.addEventListener('submit', event => {
      if (event.submitter !== submit || !renderState()) return;
      form.querySelectorAll('details.multi-filter input[type="checkbox"]:checked').forEach(input => {
        input.checked = false;
      });
      const search = form.querySelector('input[name="q"]');
      if (search) search.value = '';
    });
    renderState();
  });
};

initializeFilterForms();

const smartSortValue = cell => {
  const raw = (cell?.dataset.sortValue ?? cell?.textContent ?? '').trim();
  if (!raw || raw === '—' || raw.toLowerCase() === 'never') return {rank: 3, value: ''};
  const numeric = raw.replace(/,/g, '');
  if (/^-?\d+(?:\.\d+)?$/.test(numeric)) return {rank: 0, value: Number(numeric)};
  if (/\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b/.test(raw) || /^\d{4}-\d{2}-\d{2}/.test(raw)) {
    const date = Date.parse(raw.replace('·', ' '));
    if (!Number.isNaN(date)) return {rank: 1, value: date};
  }
  return {rank: 2, value: raw.toLocaleLowerCase()};
};

const initializeSortableTables = (root = document) => {
  root.querySelectorAll('table:not([data-sort-disabled]):not([data-sort-ready])').forEach(table => {
    table.dataset.sortReady = 'true';
    const paginationRoot = table.closest('[data-partial-root]') || table.closest('main');
    if (paginationRoot?.querySelector('.pagination')) return;
    const body = table.tBodies[0];
    const headers = [...(table.tHead?.rows[0]?.cells || [])];
    if (!body || !headers.length) return;
    [...body.rows].forEach((row, index) => { row.dataset.originalSortIndex = String(index); });
    headers.forEach((header, columnIndex) => {
      const label = header.textContent.trim();
      if (!label || label.toLocaleLowerCase() === 'actions' || header.dataset.sortDisabled !== undefined) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sortable-heading';
      button.textContent = label;
      button.setAttribute('aria-label', `Sort by ${label}`);
      header.replaceChildren(button);
      header.setAttribute('aria-sort', 'none');
      button.addEventListener('click', () => {
        const ascending = header.getAttribute('aria-sort') !== 'ascending';
        headers.forEach(item => item.setAttribute('aria-sort', item === header ? (ascending ? 'ascending' : 'descending') : 'none'));
        const rows = [...body.rows];
        rows.sort((left, right) => {
          const leftEmpty = Boolean(left.querySelector('.empty'));
          const rightEmpty = Boolean(right.querySelector('.empty'));
          if (leftEmpty !== rightEmpty) return leftEmpty ? 1 : -1;
          const a = smartSortValue(left.cells[columnIndex]);
          const b = smartSortValue(right.cells[columnIndex]);
          let result = a.rank - b.rank;
          if (!result) result = typeof a.value === 'number'
            ? a.value - b.value
            : String(a.value).localeCompare(String(b.value), undefined, {numeric: true, sensitivity: 'base'});
          if (!result) result = Number(left.dataset.originalSortIndex) - Number(right.dataset.originalSortIndex);
          return ascending ? result : -result;
        });
        rows.forEach(row => body.append(row));
      });
    });
  });
};

const clampPaneWidth = (value, min, max) => Math.min(Math.max(value, min), max);

const initializePaneResizer = ({handle, cssVariable, storageKey, min, maxWidth}) => {
  if (!handle || handle.dataset.resizerReady === 'true') return;
  handle.dataset.resizerReady = 'true';
  const stored = Number(window.localStorage.getItem(storageKey));
  if (stored) document.documentElement.style.setProperty(cssVariable, `${clampPaneWidth(stored, min, maxWidth())}px`);

  const setWidth = width => {
    const value = clampPaneWidth(width, min, maxWidth());
    document.documentElement.style.setProperty(cssVariable, `${value}px`);
    window.localStorage.setItem(storageKey, String(Math.round(value)));
  };

  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add('is-resizing-pane');
    const startX = event.clientX;
    const startWidth = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(cssVariable)) || min;
    const move = moveEvent => setWidth(startWidth + moveEvent.clientX - startX);
    const stop = () => {
      document.body.classList.remove('is-resizing-pane');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  });

  handle.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const current = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(cssVariable)) || min;
    setWidth(current + (event.key === 'ArrowRight' ? 16 : -16));
  });
};

const initializePaneResizers = (root = document) => {
  const meetingHandle = root.querySelector?.('[data-meeting-pane-resizer]') || document.querySelector('[data-meeting-pane-resizer]');
  initializePaneResizer({
    handle: meetingHandle,
    cssVariable: '--mmp-meeting-nav-width',
    storageKey: 'mmp-meeting-nav-width',
    min: 220,
    maxWidth: () => Math.min(560, window.innerWidth * .45),
  });
};

const initializeSidebarCollapse = () => {
  const toggle = document.querySelector('[data-sidebar-collapse]');
  if (!toggle || toggle.dataset.sidebarCollapseReady === 'true') return;
  toggle.dataset.sidebarCollapseReady = 'true';
  const icon = toggle.querySelector('span');
  const applyState = collapsed => {
    document.body.classList.toggle('sidebar-is-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? 'Expand navigation' : 'Collapse navigation');
    toggle.title = collapsed ? 'Expand navigation' : 'Collapse navigation';
    if (icon) icon.textContent = collapsed ? '›' : '‹';
  };
  applyState(window.localStorage.getItem('mmp-sidebar-collapsed') === 'true');
  toggle.addEventListener('click', () => {
    const collapsed = !document.body.classList.contains('sidebar-is-collapsed');
    applyState(collapsed);
    window.localStorage.setItem('mmp-sidebar-collapsed', String(collapsed));
  });
};

initializeSortableTables();
initializePaneResizers();
initializeSidebarCollapse();

document.addEventListener('click', event => {
  document.querySelectorAll('[data-multi-picker].is-open, [data-board-picker].is-open').forEach(picker => {
    if (!picker.contains(event.target)) (picker.closeMultiPicker || picker.closeBoardPicker)?.();
  });
  document.querySelectorAll('[data-single-task-picker].is-open').forEach(picker => {
    if (!picker.contains(event.target)) picker.closeSingleTaskPicker?.();
  });
});

document.addEventListener('mmp:page-loaded', event => {
  const root = event.detail?.root || document;
  initializeMessages(root);
  initializeProgressAutosave(root);
  initializeMeetingActionForms(root);
  initializeBoardPickers(root);
  initializeSingleTaskPickers(root);
  initializeFilterForms(root);
  initializeSortableTables(root);
  initializePaneResizers(root);
  if (root.matches?.('[data-live-updates-url]') || root.querySelector('[data-live-updates-url]')) {
    scheduleMeetingPoll(0);
  }
});
