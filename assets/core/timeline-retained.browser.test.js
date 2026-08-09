// Real-browser integration against interactive-fixture-server.py (RAM-only DB).
// Never point this test at a real website or production session.
const test = require('node:test');
const assert = require('node:assert/strict');
const dates = require('./timeline-interactions.js');

test('Retained Timeline: packed bundles, owner clusters, fixed tree and expanding dates survive edits/Undo', {
  skip: !process.env.MMP_LIVE_QA_URL || !process.env.MMP_LIVE_QA_SESSION,
  timeout: 180000,
}, async () => {
  const base = new URL(process.env.MMP_LIVE_QA_URL);
  assert.ok(['localhost', '127.0.0.1'].includes(base.hostname)
    && ['18081', '18082', '18083'].includes(base.port), 'Only use the disposable localhost fixture');
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 2560, height: 1440}});
  await context.addCookies([{name: 'sessionid', value: process.env.MMP_LIVE_QA_SESSION, url: base.origin}]);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const prefix = `qa-retained-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  let groupId;
  const visit = () => page.goto(`${base.origin}/tasks/timeline/?q=${encodeURIComponent(prefix)}${groupId ? `&group=${groupId}` : ''}`);
  const ready = async () => {
    await page.waitForFunction(() => {
      const root = document.querySelector('[data-timeline-root]'), viewport = document.querySelector('[data-timeline-scroll]');
      return root && !root.classList.contains('is-saving') && viewport?.getAttribute('aria-busy') !== 'true';
    });
    await page.waitForTimeout(120);
  };
  const apiPost = (url, values) => page.evaluate(async ({url, values}) => {
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
    const body = new URLSearchParams({csrfmiddlewaretoken: csrf});
    Object.entries(values).forEach(([key, value]) => Array.isArray(value) ? value.forEach(item => body.append(key, item)) : body.append(key, value));
    const response = await fetch(url, {method: 'POST', body,
      headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}});
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error(`Non-JSON fixture response: ${response.status}`);
    return {status: response.status, data: await response.json()};
  }, {url, values});
  const view = () => page.locator('[data-timeline-scroll]').evaluate(viewport => {
    const root = document.querySelector('[data-timeline-root]');
    const width = parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width'));
    return {start: root.dataset.windowStart, end: root.dataset.windowEnd, zoom: width,
      leftDay: window.MMPTimeline.ordinal(root.dataset.windowStart) + viewport.scrollLeft / width,
      days: Number(root.dataset.dayCount), tree: root.classList.contains('timeline-has-tree')};
  });
  const bar = id => page.locator(`[data-task-bar][data-task-id="${id}"]`);
  const rowTop = id => page.locator(`[data-task-row="${id}"]`).evaluate(row => parseFloat(row.style.top));
  const capture = async name => {
    if (!process.env.MMP_QA_SCREENSHOT_DIR) return;
    const fs = require('node:fs/promises'), path = require('node:path');
    await fs.mkdir(process.env.MMP_QA_SCREENSHOT_DIR, {recursive: true});
    await page.screenshot({path: path.join(process.env.MMP_QA_SCREENSHOT_DIR, name), animations: 'disabled'});
  };
  try {
    await page.goto(`${base.origin}/system-map/`);
    assert.match(await page.locator('.sidebar-user').innerText(), /QA Owner/);
    assert.equal(await page.locator('#relationship-map-data').evaluate(script => JSON.parse(script.textContent).name), 'QA System Map');
    await page.goto(`${base.origin}/tasks/timeline/`); await ready();
    assert.ok(await page.locator('[data-task-bar][data-title="QA draggable task"]').count(), 'Fixture identity is required before creating fake data');
    assert.equal(await page.locator('body').innerText().then(text => text.includes('QA private secret')), false);
    const today = await page.locator('[data-timeline-root]').getAttribute('data-today');
    const day = offset => dates.shift(today, offset);
    const scopeId = await page.locator('[data-milestone-form] [name="scopes"] option').evaluateAll(options => options.find(option => option.textContent === 'QA Scope').value);
    const groupName = `${prefix} Group`;
    assert.equal((await apiPost('/tasks/timeline/groups/add/', {name: groupName, color: '#14B8A6'})).status, 200);
    await visit(); await ready();
    groupId = await page.locator('#timeline-group-data').evaluate((script, name) => JSON.parse(script.textContent).find(group => group.name === name).id, groupName);
    const createTask = async (name, first, last, parent = '') => {
      const result = await apiPost('/tasks/add/', {modal: '1', title: `${prefix} ${name}`, scopes: [scopeId],
        status: 'todo', priority: 'medium', timeline_start_date: day(first), due_date: day(last),
        timeline_group: groupId, parent_task: parent, link_url: 'https://example.test/retained-fixture'});
      assert.equal(result.status, 200, JSON.stringify(result.data)); return result.data.task.id;
    };
    const taskA = await createTask('AlphaTask', 0, 2);
    const taskB = await createTask('BravoTask', 4, 6);
    const taskC = await createTask('CharlieTask', 1, 5, taskA);
    const createMarker = async (name, owner, offset, important = false) => {
      const result = await apiPost('/tasks/timeline/milestones/add/', {name: `${prefix} ${name}`, date: day(offset), task: owner,
        visibility: 'shared', color: '', ...(important ? {is_important: 'on'} : {})});
      assert.equal(result.status, 201, JSON.stringify(result.data)); return result.data.milestone.id;
    };
    const flagA1 = await createMarker('Alpha important', taskA, 1, true);
    const flagA2 = await createMarker('Alpha regular', taskA, 1);
    const flagC = await createMarker('Charlie separate owner', taskC, 1);
    await visit(); await ready();
    await page.locator('[data-milestone-period]').selectOption('all');
    assert.equal(await rowTop(taskA), await rowTop(taskB), 'Disjoint Tasks in a Group should share a compact lane');
    assert.notEqual(await rowTop(taskA), await rowTop(taskC), 'Overlapping Task bundles need separate lanes');
    assert.equal(await page.locator('[data-timeline-undo]').count(), 0, 'Success/Undo toast controls are removed');
    assert.equal(await page.locator('[data-timeline-feedback]').isVisible(), false);

    const alphaCluster = page.locator(`[data-milestone-cluster][data-owner="task:${taskA}"][data-date="${day(1)}"]`);
    assert.equal(await alphaCluster.locator('b').textContent(), '2');
    assert.equal(await page.locator(`.timeline-milestone[data-milestone-id="${flagC}"][data-owner="task:${taskC}"]`).count(), 1,
      'Same-date milestones of another Task must remain a separate marker');
    assert.equal(await alphaCluster.evaluate(flag => getComputedStyle(flag).fontSize), '20px');
    assert.equal(await page.locator(`.timeline-milestone[data-milestone-id="${flagC}"]`).evaluate(flag => getComputedStyle(flag).fontSize), '18px');
    await capture('timeline-retained-compact.png');
    await alphaCluster.click();
    const clusterDialog = page.locator('#timeline-cluster-dialog');
    await clusterDialog.waitFor();
    assert.match(await clusterDialog.locator('[data-cluster-title]').textContent(), new RegExp(`Group: ${groupName}.*Task: ${prefix} AlphaTask`));
    assert.deepEqual(await clusterDialog.locator('.timeline-cluster-copy strong').allTextContents(), [`${prefix} Alpha important`, `${prefix} Alpha regular`]);
    assert.equal(await clusterDialog.locator(`[data-open-milestone="${flagC}"]`).count(), 0);
    await capture('timeline-retained-cluster.png');
    await clusterDialog.locator(`[data-open-milestone="${flagA1}"]`).click();
    const milestoneForm = page.locator('[data-milestone-form]');
    await page.locator('#timeline-milestone-dialog[open]').waitFor();
    assert.equal(await milestoneForm.locator('[name="name"]').inputValue(), `${prefix} Alpha important`);
    assert.equal(await milestoneForm.locator('[data-milestone-task-search]').count(), 0);
    assert.equal(await milestoneForm.locator('[name="task"]').isVisible(), false);
    const picker = milestoneForm.locator('[data-single-task-picker]');
    await picker.locator('.board-picker-toggle').click();
    await picker.locator('[data-single-task-search]').fill(`${prefix} AlphaTask`);
    assert.equal(await picker.locator('.single-task-option:visible').count(), 1);
    await picker.locator('[data-single-task-search]').press('Enter');
    assert.equal(await milestoneForm.locator('[name="task"]').inputValue(), String(taskA));
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await page.locator('[data-important-only]').check();
    assert.equal(await page.locator(`[data-milestone-cluster][data-owner="task:${taskA}"]`).count(), 0);
    assert.equal(await page.locator(`.timeline-milestone[data-milestone-id="${flagA1}"]`).count(), 1);
    assert.equal(await page.locator(`.timeline-milestone[data-milestone-id="${flagA2}"]`).count(), 0);
    assert.equal(await page.locator(`.timeline-milestone[data-milestone-id="${flagC}"]`).count(), 0);
    await page.locator('[data-important-only]').uncheck();
    console.log('PASS compact packing, same-owner/date clusters, scoped popup, internal picker and Important filter');

    await createMarker('Alpha outside bar', taskA, 5);
    await visit(); await ready(); await page.locator('[data-milestone-period]').selectOption('all');
    assert.notEqual(await rowTop(taskA), await rowTop(taskB), 'A milestone beyond A\'s bar must prevent it overlapping B');
    const beforeTree = await view();
    await page.locator('[data-toggle-timeline-tree]').click();
    await page.waitForFunction(() => document.querySelector('[data-timeline-root]').classList.contains('timeline-has-tree'));
    const afterTree = await view();
    assert.equal(afterTree.zoom, beforeTree.zoom); assert.ok(Math.abs(afterTree.leftDay - beforeTree.leftDay) < .02);
    const orderedRows = await page.locator('[data-task-row]').evaluateAll(rows => rows.map(row => ({id: Number(row.dataset.taskRow), y: parseFloat(row.style.top)})).sort((a, b) => a.y - b.y).map(row => row.id));
    assert.deepEqual(orderedRows, [taskA, taskC, taskB]);
    assert.equal(new Set(await Promise.all([rowTop(taskA), rowTop(taskB), rowTop(taskC)])).size, 3);
    assert.equal(await page.locator(`[data-task-row="${taskC}"]`).evaluate(row => row.style.getPropertyValue('--task-depth')), '1');
    for (const id of [taskA, taskB, taskC]) {
      assert.equal(await bar(id).locator('.timeline-bar-title').evaluate(title => getComputedStyle(title).visibility), 'hidden');
    }
    await capture('timeline-retained-tree.png');
    const label = page.locator(`[data-task-row="${taskA}"] .timeline-task-label`);
    const labelX = (await label.boundingBox()).x;
    await page.locator('[data-timeline-scroll]').evaluate(viewport => { viewport.scrollLeft += 42 * 8; });
    await page.waitForTimeout(150);
    assert.ok(Math.abs((await label.boundingBox()).x - labelX) < 1, 'The left tree must stay fixed while dates scroll');
    const beforeCloseTree = await view();
    await page.locator('[data-toggle-timeline-tree]').click();
    const afterCloseTree = await view();
    assert.equal(afterCloseTree.zoom, beforeCloseTree.zoom); assert.ok(Math.abs(afterCloseTree.leftDay - beforeCloseTree.leftDay) < .02);
    assert.equal(await bar(taskA).locator('.timeline-bar-title').evaluate(title => getComputedStyle(title).visibility), 'visible');
    console.log('PASS task+milestone collision, hierarchy rows, fixed tree and unchanged date/zoom when toggling');

    const initialExtent = await view(), stableLanes = await Promise.all([rowTop(taskA), rowTop(taskB), rowTop(taskC)]);
    const future = day(730);
    for (let index = 0; index < 8 && (await view()).end < future; index++) {
      const previous = await view();
      const anchor = await page.locator('[data-timeline-scroll]').evaluate(viewport => {
        viewport.scrollLeft = viewport.scrollWidth;
        return window.MMPTimeline.ordinal(document.querySelector('[data-timeline-root]').dataset.windowStart)
          + viewport.scrollLeft / parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width'));
      });
      await page.waitForFunction(end => document.querySelector('[data-timeline-root]').dataset.windowEnd > end, previous.end);
      await ready();
      const next = await view();
      assert.equal(next.start, initialExtent.start, 'Appending years must preserve the beginning already opened');
      assert.ok(Math.abs(next.leftDay - anchor) < .05, 'Appending time must not move the viewed date');
    }
    const retainedFuture = await view(); assert.ok(retainedFuture.end >= future, 'Test must actually expand at least two future years');
    const prependAnchor = await page.locator('[data-timeline-scroll]').evaluate(viewport => {
      viewport.scrollLeft = 0;
      return window.MMPTimeline.ordinal(document.querySelector('[data-timeline-root]').dataset.windowStart);
    });
    await page.waitForFunction(start => document.querySelector('[data-timeline-root]').dataset.windowStart < start, retainedFuture.start);
    await ready();
    const retained = await view();
    assert.equal(retained.end, retainedFuture.end, 'Prepending past time must keep all future years');
    assert.ok(Math.abs(retained.leftDay - prependAnchor) < .05, 'Prepending keeps the same absolute date anchor');
    assert.deepEqual(await Promise.all([rowTop(taskA), rowTop(taskB), rowTop(taskC)]), stableLanes, 'Panning cannot repack Tasks');
    assert.ok(await page.locator('[data-calendar-day]').count() < 241, 'Retained years do not require mounting all date cells');
    assert.ok(retained.days > 730);
    console.log('PASS two-year extension, retained old dates, past prepend, stable anchor/lanes and virtual calendar cells');

    await page.locator('[data-scroll-today]').click(); await ready();
    const beforeDrag = await view(), originalStart = await bar(taskA).getAttribute('data-start');
    const box = await bar(taskA).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + beforeDrag.zoom, box.y + box.height / 2, {steps: 8}); await page.mouse.up();
    await page.waitForFunction(({id, start}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start !== start, {id: taskA, start: originalStart});
    await ready();
    assert.deepEqual({start: (await view()).start, end: (await view()).end}, {start: beforeDrag.start, end: beforeDrag.end});
    assert.equal(await page.locator('[data-timeline-feedback]').isVisible(), false, 'Saved toast must remain absent');
    await page.keyboard.press('Control+z');
    await page.waitForFunction(({id, start}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start === start, {id: taskA, start: originalStart});
    await ready();
    assert.deepEqual({start: (await view()).start, end: (await view()).end}, {start: beforeDrag.start, end: beforeDrag.end});
    assert.equal(await page.locator('[data-timeline-feedback]').isVisible(), false, 'Undone toast must remain absent');
    await bar(taskA).click();
    const taskForm = page.locator('[data-shared-task-form]'); await taskForm.waitFor();
    await taskForm.locator('[name="title"]').fill(`${prefix} AlphaTask edited`);
    await taskForm.locator('[type="submit"]').click();
    await page.waitForFunction(id => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.title.endsWith('AlphaTask edited'), taskA);
    await ready();
    assert.deepEqual({start: (await view()).start, end: (await view()).end}, {start: beforeDrag.start, end: beforeDrag.end});
    assert.equal(await page.locator('[data-timeline-feedback]').isVisible(), false);
    assert.equal(await page.locator('#timeline-milestone-data').evaluate(script => JSON.parse(script.textContent).some(item => item.name === 'QA private secret')), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('PASS date drag, Ctrl+Z and shared-form edit retain full extent with no success/Undo toast or private leak');
  } catch (error) {
    const state = await view().catch(() => ({}));
    const feedback = await page.locator('[data-feedback-text]').textContent().catch(() => '');
    error.message += `\nFixture state: ${JSON.stringify(state)}; feedback: ${feedback}; JS errors: ${JSON.stringify(errors)}`;
    throw error;
  } finally { await browser.close(); }
});
