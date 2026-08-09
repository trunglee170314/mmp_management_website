// Opt-in integration checks for the disposable interactive-fixture-server.py ONLY.
// MMP_LIVE_QA_URL=http://127.0.0.1:18082 MMP_LIVE_QA_SESSION=<fixture cookie>
// node --test assets/core/timeline.browser.test.js
// This seeds uniquely named FAKE tasks/milestones in the fixture's in-memory DB.
const test = require('node:test');
const assert = require('node:assert/strict');

const fixtureUrl = process.env.MMP_LIVE_QA_URL;
const fixtureSession = process.env.MMP_LIVE_QA_SESSION || process.env.QA_SESSION;

test('Timeline live fixture: shared forms, date-window anchors, metadata and lazy rows', {
  skip: !fixtureUrl || !fixtureSession,
  timeout: 180000,
}, async () => {
  const base = new URL(fixtureUrl);
  assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname)
    && ['18081', '18082', '18083'].includes(base.port),
  'Use a disposable localhost fixture, never a production URL');
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 1500, height: 950}});
  await context.addCookies([{name: 'sessionid', value: fixtureSession, url: base.origin}]);
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const prefix = `qa-live-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  let scopedGroups = [];
  const visit = async query => {
    const params = new URLSearchParams(); if (query) params.set('q', query);
    scopedGroups.forEach(group => params.append('group', group));
    await page.goto(`${base.origin}/tasks/timeline/?${params}`);
    await page.waitForFunction(() => document.querySelector('[data-timeline-scroll]')?.getAttribute('aria-busy') !== 'true');
  };
  const settled = () => page.waitForFunction(() => !document.querySelector('[data-timeline-root]').classList.contains('is-saving'));
  const apiPost = async (url, values) => page.evaluate(async ({url, values}) => {
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
    const body = new URLSearchParams({csrfmiddlewaretoken: csrf});
    for (const [name, value] of Object.entries(values)) {
      if (Array.isArray(value)) value.forEach(item => body.append(name, item));
      else body.append(name, value);
    }
    const response = await fetch(url, {method: 'POST', body,
      headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}});
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
      throw new Error(`Fixture returned non-JSON ${response.status}`);
    }
    return {status: response.status, data: await response.json()};
  }, {url, values});
  try {
    await visit();
    await page.locator('[data-timeline-root]').waitFor();
    assert.ok((await page.locator('.sidebar-user').innerText()).includes('QA Owner'),
      'The signed-in user must be the disposable fixture QA Owner');
    assert.ok(await page.locator('[data-task-bar][data-title="QA draggable task"]').count(),
      'Fixture identity check failed; do not seed another database');
    assert.equal((await page.locator('body').innerText()).includes('QA private secret'), false);
    for (const text of ['Past dates do not mean completed.', 'Click an empty day to create', 'Only milestones you can access are included.']) {
      assert.equal((await page.locator('body').innerText()).includes(text), false);
    }
    const today = await page.locator('[data-timeline-root]').getAttribute('data-today');
    const taskDay = await page.evaluate(today => window.MMPTimeline.shift(today, 11), today);
    await page.locator(`[data-calendar-day="${taskDay}"]`).click();
    assert.equal(await page.locator('[data-timeline-create-menu]').isVisible(), true);
    assert.equal(await page.locator('[data-create-kind]').count(), 4);
    await page.locator('[data-create-kind="task"]').click();
    const form = page.locator('[data-shared-task-form]');
    await form.waitFor();
    assert.equal(await form.locator('[name="timeline_start_date"]').inputValue(), taskDay);
    assert.equal(await form.locator('[name="due_date"]').inputValue(), taskDay);
    assert.deepEqual(await form.locator('[name="title"]').evaluate(input => ({
      size: getComputedStyle(input).fontSize, weight: getComputedStyle(input).fontWeight,
    })), {size: '14px', weight: '400'});
    await form.locator('[name="title"]').fill(`${prefix} UI task`);
    await form.locator('[name="link_url"]').fill('https://example.test/live-fixture');
    await form.locator('#scopes-picker-toggle').click();
    const scope = form.locator('input[name="scopes"]').first();
    const scopeId = await scope.getAttribute('value');
    await scope.check();
    await form.locator('[name="title"]').click();
    const saveResponse = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/tasks/add/');
    await form.locator('[type="submit"]').click();
    const saved = await saveResponse;
    assert.equal(saved.ok(), true, await saved.text());
    const uiTaskId = (await saved.json()).task.id;
    await settled();
    await page.locator(`[data-task-bar][data-task-id="${uiTaskId}"]`).waitFor();
    console.log('PASS shared Add Task form, date prefilling, picker and normal 14px text');

    await visit(`${prefix} UI task`);
    const taskBar = page.locator(`[data-task-bar][data-task-id="${uiTaskId}"]`);
    await taskBar.scrollIntoViewIfNeeded();
    const originalColumn = await taskBar.evaluate(bar => bar.style.gridColumn);
    const originalStart = await taskBar.getAttribute('data-start');
    const dayWidth = await page.locator('[data-timeline-scroll]').evaluate(viewport =>
      parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width')));
    const dragBox = await taskBar.boundingBox();
    let releaseSchedule, signalSchedule;
    const scheduleStarted = new Promise(resolve => { signalSchedule = resolve; });
    const scheduleRoute = url => url.pathname === `/tasks/timeline/tasks/${uiTaskId}/`;
    await page.route(scheduleRoute, async route => {
      await new Promise(resolve => { releaseSchedule = resolve; signalSchedule(); });
      await route.continue();
    });
    try {
      await page.mouse.move(dragBox.x + dragBox.width / 2, dragBox.y + dragBox.height / 2);
      await page.mouse.down();
      await page.mouse.move(dragBox.x + dragBox.width / 2 + dayWidth, dragBox.y + dragBox.height / 2, {steps: 8});
      await page.mouse.up();
      await page.waitForFunction(() => document.querySelector('[data-timeline-root]').classList.contains('is-saving'));
      await scheduleStarted;
      assert.notEqual(await taskBar.evaluate(bar => bar.style.gridColumn), originalColumn,
        'Dropped preview must remain at its new date while the server is pending');
    } finally {
      releaseSchedule?.();
    }
    await page.waitForFunction(({id, before}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start !== before,
      {id: uiTaskId, before: originalStart});
    await settled();
    await page.unroute(scheduleRoute);
    await page.waitForTimeout(100);
    await page.keyboard.press('Control+z');
    await page.waitForFunction(({id, before}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start === before,
      {id: uiTaskId, before: originalStart});
    await settled();
    console.log('PASS pending drag preview and immediate keyboard Undo after refresh');

    const destinationName = `${prefix} destination`;
    assert.equal((await apiPost('/tasks/timeline/groups/add/', {name: destinationName, color: '#14B8A6'})).status, 200);
    await visit(`${prefix} UI task`);
    const destinationId = await page.evaluate(name => JSON.parse(document.querySelector('#timeline-group-data').textContent).find(group => group.name === name).id, destinationName);
    scopedGroups = [String(destinationId), 'ungrouped'];
    const transferMarker = await apiPost('/tasks/timeline/milestones/add/', {name: `${prefix} child marker`, date: taskDay, task: uiTaskId, visibility: 'shared', color: ''});
    assert.equal(transferMarker.status, 201);
    await visit(`${prefix} UI task`);
    await page.locator('[data-milestone-period]').selectOption('all');
    const beforeTransferStart = await taskBar.getAttribute('data-start'), beforeTransferDue = await taskBar.getAttribute('data-due');
    const destination = page.locator(`[data-timeline-section][data-group="${destinationId}"]`);
    await destination.locator('[data-group-toggle]').click();
    assert.equal(await destination.locator('[data-group-body]').isVisible(), false);
    const groupHeader = await destination.locator('.timeline-group-row').boundingBox();
    await taskBar.scrollIntoViewIfNeeded();
    const transferBox = await taskBar.boundingBox();
    const transferX = transferBox.x + transferBox.width / 2;
    const chartTop = await page.locator('.timeline-panel').evaluate(panel => panel.getBoundingClientRect().top);
    await page.mouse.move(transferX, transferBox.y + transferBox.height / 2); await page.mouse.down();
    await page.mouse.move(transferX, groupHeader.y + groupHeader.height / 2, {steps: 8});
    assert.equal(await destination.evaluate(section => section.classList.contains('is-drop-target')), true);
    assert.equal(await page.locator('.timeline-drag-ghost').isVisible(), true);
    await page.mouse.up();
    await page.waitForFunction(({id, group}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.group === String(group), {id: uiTaskId, group: destinationId});
    await settled();
    assert.equal(await taskBar.getAttribute('data-start'), beforeTransferStart, 'Vertical-only movement must keep Start');
    assert.equal(await taskBar.getAttribute('data-due'), beforeTransferDue, 'Vertical-only movement must keep Due');
    const groupBranch = page.locator(`[data-milestone-branch="group:${destinationId}"]`);
    assert.equal(await groupBranch.locator(`[data-milestone-branch="task:${uiTaskId}"] [data-jump-milestone="${transferMarker.data.milestone.id}"]`).count(), 1, 'Task milestones must follow their Task into the destination Group');
    const toast = page.locator('[data-timeline-feedback]');
    assert.equal(await toast.isVisible(), false, 'Success must not show a Saved/Undo toast');
    assert.equal(await page.locator('[data-timeline-undo]').count(), 0);
    await page.keyboard.press('Control+z');
    await page.waitForFunction(id => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.group === '', uiTaskId);
    await settled();
    assert.equal(await taskBar.getAttribute('data-start'), beforeTransferStart);
    assert.equal(await taskBar.getAttribute('data-due'), beforeTransferDue);
    assert.equal(await page.locator(`[data-milestone-branch="ungrouped"] [data-milestone-branch="task:${uiTaskId}"]`).count(), 1);
    // Preset dialogs are shared with context-menu creation and preserve legacy colors.
    await destination.locator('[data-edit-group]').click();
    assert.equal(await page.locator('[data-group-form] [data-admin-color]').count(), 8);
    assert.equal(await page.locator('[data-group-form] input[type="color"]').count(), 0);
    await page.locator('[data-group-form] [data-admin-color="#EC4899"]').click();
    assert.equal(await page.locator('[data-group-form] [name="color"]').inputValue(), '#EC4899');
    await page.keyboard.press('Escape');
    assert.equal((await page.locator('body').innerText()).includes('QA private secret'), false);
    console.log('PASS collapsed Group drop, date-preserving transfer, milestone hierarchy, presets and keyboard Undo without toast');

    const otherTask = await apiPost('/tasks/add/', {
      modal: '1', title: `${prefix} B task`, scopes: [scopeId], status: 'todo', priority: 'medium',
      due_date: taskDay, link_url: 'https://example.test/race-fixture',
    });
    assert.equal(otherTask.status, 200);
    const taskB = otherTask.data.task.id;
    await visit(prefix);
    const dueOnlyBar = page.locator(`[data-task-bar][data-task-id="${taskB}"]`);
    const dueOnlyBox = await dueOnlyBar.boundingBox();
    const dueOnlyHeader = await destination.locator('.timeline-group-row').boundingBox();
    const dueOnlyX = dueOnlyBox.x + dueOnlyBox.width / 2;
    await page.mouse.move(dueOnlyX, dueOnlyBox.y + dueOnlyBox.height / 2); await page.mouse.down();
    await page.mouse.move(dueOnlyX, dueOnlyHeader.y + dueOnlyHeader.height / 2, {steps: 8}); await page.mouse.up();
    await page.waitForFunction(({id, group}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.group === String(group), {id: taskB, group: destinationId});
    await settled();
    assert.equal(await dueOnlyBar.getAttribute('data-start'), '', 'Vertical transfer must not invent a missing Start Date');
    assert.equal(await dueOnlyBar.getAttribute('data-due'), taskDay);
    await page.keyboard.press('Control+z');
    await page.waitForFunction(id => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.group === '', taskB);
    await settled();

    await page.locator('[data-timeline-scroll]').evaluate((viewport, width) => { viewport.scrollLeft += width * 8; }, dayWidth);
    const resizeBox = await dueOnlyBar.locator('[data-resize="due"]').boundingBox(), resizeHeader = await destination.locator('.timeline-group-row').boundingBox();
    const resizeDue = await page.evaluate(day => window.MMPTimeline.shift(day, 2), taskDay);
    await page.mouse.move(resizeBox.x + resizeBox.width / 2, resizeBox.y + resizeBox.height / 2);
    assert.equal(await page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.dataset.resize, {x: resizeBox.x + resizeBox.width / 2, y: resizeBox.y + resizeBox.height / 2}), 'due');
    await page.mouse.down();
    await page.mouse.move(resizeBox.x + resizeBox.width / 2 + dayWidth * 2, resizeHeader.y + resizeHeader.height / 2, {steps: 8});
    assert.match(await page.locator('[data-drag-tooltip]').textContent(), new RegExp(resizeDue));
    await page.mouse.up();
    await page.waitForFunction(({id, due}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.due === due, {id: taskB, due: resizeDue});
    await settled();
    assert.equal(await dueOnlyBar.getAttribute('data-group'), '', 'Resizing must not transfer a Task to another Group');
    await page.keyboard.press('Control+z');
    await page.waitForFunction(({id, due}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.due === due, {id: taskB, due: taskDay});
    await settled();
    assert.equal(await dueOnlyBar.getAttribute('data-start'), '');
    const cancelRevision = await dueOnlyBar.getAttribute('data-revision'), cancelBox = await dueOnlyBar.boundingBox();
    await page.mouse.move(cancelBox.x + cancelBox.width / 2, cancelBox.y + cancelBox.height / 2); await page.mouse.down();
    await page.mouse.move(cancelBox.x + cancelBox.width / 2, resizeHeader.y + resizeHeader.height / 2, {steps: 6});
    await page.keyboard.press('Escape'); await page.mouse.up();
    assert.equal(await dueOnlyBar.getAttribute('data-revision'), cancelRevision, 'Escape must cancel a transfer without saving');
    assert.equal(await page.locator('.timeline-drag-ghost,.is-drop-target').count(), 0);
    console.log('PASS due-only transfer, resize isolation and Escape cancellation');
    const delayedForm = url => url.pathname === `/tasks/${uiTaskId}/edit/` && url.searchParams.get('modal') === '1';
    await page.route(delayedForm, async route => {
      const response = await route.fetch();
      await new Promise(resolve => setTimeout(resolve, 700));
      try { await route.fulfill({response}); } catch (_) { /* Superseded request was correctly aborted. */ }
    });
    await page.locator(`[data-task-bar][data-task-id="${uiTaskId}"]`).click();
    await page.waitForTimeout(100);
    await page.keyboard.press('Escape');
    await page.locator(`[data-task-bar][data-task-id="${taskB}"]`).click();
    await form.waitFor();
    await page.waitForTimeout(850);
    assert.equal(new URL(await form.getAttribute('action'), base.origin).pathname, `/tasks/${taskB}/edit/`);
    await page.keyboard.press('Escape');
    await page.unroute(delayedForm);
    console.log('PASS delayed modal A cannot overwrite newly opened modal B');

    for (let index = 0; index < 4; index++) {
      const previous = await page.locator('[data-timeline-root]').getAttribute('data-window-end');
      const retainedStart = await page.locator('[data-timeline-root]').getAttribute('data-window-start');
      const anchor = await page.locator('[data-timeline-scroll]').evaluate(viewport => {
        viewport.scrollLeft = viewport.scrollWidth;
        return window.MMPTimeline.ordinal(document.querySelector('[data-timeline-root]').dataset.windowStart)
          + viewport.scrollLeft / parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width'));
      });
      await page.waitForFunction(previous => document.querySelector('[data-timeline-root]').dataset.windowEnd > previous, previous);
      await page.waitForFunction(() => document.querySelector('[data-timeline-scroll]').getAttribute('aria-busy') !== 'true');
      assert.equal(await page.locator('[data-timeline-root]').getAttribute('data-window-start'), retainedStart, 'Scrolling right must retain the old start');
      const restored = await page.locator('[data-timeline-scroll]').evaluate(viewport =>
        window.MMPTimeline.ordinal(document.querySelector('[data-timeline-root]').dataset.windowStart)
          + viewport.scrollLeft / parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width')));
      assert.ok(Math.abs(anchor - restored) < 0.05, `Date anchor moved by ${anchor - restored} days`);
      assert.ok(await page.locator('[data-calendar-day]').count() < 220, 'Calendar cells remain virtualized without shrinking the scroll extent');
    }
    await page.locator('[data-scroll-today]').click();
    await page.waitForTimeout(350);
    console.log('PASS timeline appends future dates, retains all earlier years and virtualizes the visible calendar');

    const marker = await apiPost('/tasks/timeline/milestones/add/', {
      name: `${prefix} external marker`, date: today, task: uiTaskId, visibility: 'shared', color: '',
    });
    assert.equal(marker.status, 201);
    let previous = await page.locator('[data-timeline-root]').getAttribute('data-window-end');
    await page.locator('[data-timeline-scroll]').evaluate(viewport => { viewport.scrollLeft = viewport.scrollWidth; });
    await page.waitForFunction(previous => document.querySelector('[data-timeline-root]').dataset.windowEnd > previous, previous);
    await page.waitForFunction(() => document.querySelector('[data-timeline-scroll]').getAttribute('aria-busy') !== 'true');
    await page.locator('[data-scroll-today]').click();
    await page.locator(`.timeline-milestone[data-milestone-id="${marker.data.milestone.id}"]`).waitFor();
    await page.locator(`.timeline-milestone[data-milestone-id="${marker.data.milestone.id}"]`).click();
    assert.equal(await page.locator('[data-milestone-form] [name="name"]').inputValue(), `${prefix} external marker`);
    assert.match(await page.locator('[data-milestone-form]').getAttribute('action'), /\/edit\//);
    assert.equal(await page.locator('[data-milestone-group-field]').isVisible(), false,
      'A Timeline milestone must not show the Group selector');
    assert.equal(await page.locator('[data-milestone-task-field]').isVisible(), true,
      'A Task milestone must show the searchable Task selector');
    await page.keyboard.press('Escape');
    console.log('PASS a new milestone is editable after chart-only metadata refresh');

    const scheduledPrefix = `${prefix}-scheduled`;
    const unscheduledPrefix = `${prefix}-unscheduled`;
    const seed = await page.evaluate(async ({scheduledPrefix, unscheduledPrefix, scopeId, today}) => {
      const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
      let lastTask;
      for (const [prefix, count, scheduled] of [[scheduledPrefix, 102, true], [unscheduledPrefix, 55, false]]) {
        for (let index = 0; index < count; index++) {
          const body = new URLSearchParams({csrfmiddlewaretoken: csrf, modal: '1', title: `${prefix} ${index}`,
            scopes: scopeId, status: 'todo', priority: 'medium', link_url: `https://example.test/seed/${index}`});
          if (scheduled) { body.set('timeline_start_date', today); body.set('due_date', today); }
          const response = await fetch('/tasks/add/', {method: 'POST', body, headers: {'Accept': 'application/json'}});
          if (!response.ok) throw new Error(`Fake fixture seeding failed ${response.status}`);
          if (scheduled) lastTask = (await response.json()).task.id;
        }
      }
      return {lastTask};
    }, {scheduledPrefix, unscheduledPrefix, scopeId, today});
    const lastMarker = await apiPost('/tasks/timeline/milestones/add/', {
      name: `${prefix} last task marker`, date: today, task: seed.lastTask, visibility: 'shared', color: '',
    });
    assert.equal(lastMarker.status, 201);
    await visit(scheduledPrefix);
    await page.waitForTimeout(250);
    assert.equal(await page.locator('[data-task-row]').count(), 100);
    assert.equal(await page.locator(`[data-milestone-id="${lastMarker.data.milestone.id}"]`).count(), 0);
    await page.locator('[data-timeline-scroll]').evaluate(viewport => { viewport.scrollTop = viewport.scrollHeight; });
    await page.waitForFunction(() => document.querySelectorAll('[data-task-row]').length === 102);
    assert.equal(await page.locator(`[data-milestone-id="${lastMarker.data.milestone.id}"]`).count(), 1);
    assert.equal(await page.locator('[data-task-row]').evaluateAll(rows => new Set(rows.map(row => row.dataset.taskRow)).size), 102);
    await visit(unscheduledPrefix);
    await page.waitForTimeout(600);
    assert.equal(await page.locator('[data-task-row]').count(), 0);
    assert.equal(await page.locator('[data-unscheduled-row]').count(), 50);
    await page.locator('[data-load-unscheduled]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-unscheduled-row]').length === 55);
    assert.equal(await page.locator('[data-unscheduled-row]').evaluateAll(rows => new Set(rows.map(row => row.dataset.unscheduledRow)).size), 55);
    assert.equal(await page.locator('[data-load-unscheduled]').isVisible(), false);
    console.log('PASS scheduled lazy rows and independent Unscheduled pagination without duplicates');

    if (process.env.MMP_QA_SCREENSHOT_DIR) {
      const fs = require('node:fs/promises'), path = require('node:path');
      await fs.mkdir(process.env.MMP_QA_SCREENSHOT_DIR, {recursive: true});
      await visit(prefix); await page.locator('[data-milestone-period]').selectOption('all');
      await page.screenshot({path: path.join(process.env.MMP_QA_SCREENSHOT_DIR, 'timeline.png'), fullPage: true});
    }

    await page.setViewportSize({width: 390, height: 844});
    await visit(`${prefix} UI task`);
    await page.locator(`[data-task-bar][data-task-id="${uiTaskId}"]`).click();
    await form.waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), true);
    const modalBox = await page.locator('#timeline-create-task-dialog').boundingBox();
    assert.ok(modalBox.x >= 0 && modalBox.x + modalBox.width <= 390);
    assert.equal(await form.locator('[name="title"]').evaluate(input => getComputedStyle(input).fontSize), '14px');
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    console.log('PASS compact mobile modal and no JavaScript errors');
  } catch (error) {
    const diagnostic = await page.evaluate(() => {
      const root = document.querySelector('[data-timeline-root]'), viewport = document.querySelector('[data-timeline-scroll]');
      return {window: root?.dataset.windowStart, saving: root?.classList.contains('is-saving'),
        busy: viewport?.getAttribute('aria-busy'), scroll: viewport && [viewport.scrollLeft, viewport.scrollWidth, viewport.clientWidth, viewport.scrollTop],
        dialogs: [...document.querySelectorAll('dialog[open]')].map(dialog => dialog.id),
        feedback: document.querySelector('[data-feedback-text]')?.textContent};
    }).catch(() => ({}));
    error.message += `\nQA state: ${JSON.stringify(diagnostic)}; JS errors: ${JSON.stringify(errors)}`;
    throw error;
  } finally {
    await browser.close();
  }
});
