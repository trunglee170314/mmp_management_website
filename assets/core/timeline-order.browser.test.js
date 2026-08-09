// Native mouse/keyboard regressions against the RAM-only interactive QA fixture.
// Never use a production URL or production session cookie.
const test = require('node:test');
const assert = require('node:assert/strict');
const dates = require('./timeline-interactions.js');

test('Timeline manual order: exact A/B/C compact rows, persistence, Undo and true parent branches', {
  skip: !process.env.MMP_LIVE_QA_URL || !process.env.MMP_LIVE_QA_SESSION,
  timeout: 180000,
}, async () => {
  const base = new URL(process.env.MMP_LIVE_QA_URL);
  assert.ok(['localhost', '127.0.0.1'].includes(base.hostname)
    && ['18081', '18082', '18083'].includes(base.port), 'Use the disposable localhost fixture only');
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 2560, height: 1440}, reducedMotion: 'reduce'});
  await context.addCookies([{name: 'sessionid', value: process.env.MMP_LIVE_QA_SESSION, url: base.origin}]);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() === 'POST') writes.push(new URL(request.url()).pathname); });
  const prefix = `qa-order-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  let groupId, today, scopeId;
  const visit = () => page.goto(`${base.origin}/tasks/timeline/?q=${encodeURIComponent(prefix)}${groupId ? `&group=${groupId}` : ''}`);
  const ready = async () => {
    await page.waitForFunction(() => {
      const root = document.querySelector('[data-timeline-root]'), viewport = document.querySelector('[data-timeline-scroll]');
      return root && !root.classList.contains('is-saving') && viewport?.getAttribute('aria-busy') !== 'true';
    });
    await page.waitForTimeout(100);
  };
  const apiPost = (url, values) => page.evaluate(async ({url, values}) => {
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
    const body = new URLSearchParams({csrfmiddlewaretoken: csrf});
    Object.entries(values).forEach(([key, value]) => Array.isArray(value)
      ? value.forEach(item => body.append(key, item)) : body.append(key, value));
    const response = await fetch(url, {method: 'POST', body,
      headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}});
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error(`Fixture returned non-JSON ${response.status}`);
    return {status: response.status, data: await response.json()};
  }, {url, values});
  const day = offset => dates.shift(today, offset);
  const row = id => page.locator(`[data-task-row="${id}"]`);
  const bar = id => page.locator(`[data-task-bar][data-task-id="${id}"]`);
  const top = id => row(id).evaluate(element => Number.parseFloat(element.style.top));
  const geometryState = id => bar(id).evaluate(element => ({
    start: element.dataset.start, due: element.dataset.due, group: element.dataset.group,
  }));
  const disclosureState = locator => locator.evaluate(button => {
    const icon = button.querySelector('.timeline-disclosure-icon'), style = getComputedStyle(icon, '::before');
    const bounds = icon.getBoundingClientRect(), parent = button.getBoundingClientRect();
    return {expanded: button.getAttribute('aria-expanded'), text: icon.textContent,
      width: bounds.width, height: bounds.height, offsetY: (bounds.y + bounds.height / 2) - (parent.y + parent.height / 2),
      rotation: new DOMMatrix(style.transform).b, color: getComputedStyle(icon).color, content: style.content};
  });
  const canonical = () => page.locator('#timeline-task-data').evaluate(script =>
    window.MMPTimelineLayout.hierarchy(JSON.parse(script.textContent)).map(task => Number(task.id)));
  const expectOrder = async expected => {
    await page.waitForFunction(expected => {
      const script = document.querySelector('#timeline-task-data');
      return script && JSON.stringify(window.MMPTimelineLayout.hierarchy(JSON.parse(script.textContent)).map(task => Number(task.id))) === JSON.stringify(expected);
    }, expected);
    await ready(); assert.deepEqual(await canonical(), expected);
  };
  const setTree = async expanded => {
    if (await page.locator('[data-timeline-root]').evaluate(root => root.classList.contains('timeline-has-tree')) !== expanded) {
      await page.locator('[data-toggle-timeline-tree]').click();
    }
    await ready();
  };
  const createTask = async (name, first, last, parent = '') => {
    const result = await apiPost('/tasks/add/', {
      modal: '1', title: `${prefix} ${name}`, scopes: [scopeId], status: 'todo', priority: 'medium',
      timeline_group: groupId, parent_task: parent, link_url: 'https://example.test/order-fixture',
      ...(first == null ? {} : {timeline_start_date: day(first), due_date: day(last)}),
    });
    assert.equal(result.status, 200, JSON.stringify(result.data)); return result.data.task.id;
  };
  const marker = async (name, task, offset) => {
    const result = await apiPost('/tasks/timeline/milestones/add/', {
      name: `${prefix} ${name}`, date: day(offset), task, visibility: 'shared', color: '',
    });
    assert.equal(result.status, 201, JSON.stringify(result.data)); return result.data.milestone.id;
  };
  const dropBarAtRow = async (source, target, invalid = false) => {
    const sourceBox = await bar(source).boundingBox(), targetBox = await row(target).boundingBox();
    const x = sourceBox.x + sourceBox.width / 2;
    await page.mouse.move(x, sourceBox.y + sourceBox.height / 2); await page.mouse.down();
    await page.mouse.move(x, targetBox.y + 33, {steps: 10});
    assert.equal(await page.locator(`.timeline-order-drop-line${invalid ? '.is-invalid' : ':not(.is-invalid)'}`).count(), 1,
      'A vertical drag must preview the intended manual row target');
    await page.mouse.up();
    await ready();
  };
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
    assert.ok(await page.locator('[data-task-bar][data-title="QA draggable task"]').count(), 'Fixture identity required before fake seeding');
    today = await page.locator('[data-timeline-root]').getAttribute('data-today');
    scopeId = await page.locator('[data-milestone-form] [name="scopes"] option').evaluateAll(options => options.find(option => option.textContent === 'QA Scope').value);
    const groupName = `${prefix} Group`;
    assert.equal((await apiPost('/tasks/timeline/groups/add/', {name: groupName, color: '#14B8A6'})).status, 200);
    await visit(); await ready();
    groupId = await page.locator('#timeline-group-data').evaluate((script, name) => JSON.parse(script.textContent).find(group => group.name === name).id, groupName);
    const a = await createTask('A', 0, 4), b = await createTask('B', 2, 6), c = await createTask('C', 7, 9);
    await visit(); await ready(); await setTree(false);
    assert.ok(await bar(c).getAttribute('data-order-revision'), 'Fixture must have the manual-order backend loaded');
    await expectOrder([a, b, c]);
    assert.equal(await top(a), 0); assert.equal(await top(b), 52); assert.equal(await top(c), 52,
      'C defaults to the nearest preceding available Task row B, not the topmost row A');
    const before = await geometryState(c);
    await capture('timeline-order-before-ABC.png');
    await dropBarAtRow(c, a);
    await expectOrder([a, c, b]);
    assert.equal(await top(c), await top(a)); assert.notEqual(await top(c), await top(b));
    assert.deepEqual(await geometryState(c), before, 'Manual vertical movement changes no Task date or Group');
    assert.ok(writes.includes(`/tasks/timeline/tasks/${c}/order/`));
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await expectOrder([a, b, c]);
    assert.equal(await top(c), await top(b));
    await dropBarAtRow(c, a); await expectOrder([a, c, b]);
    await setTree(true);
    assert.ok(await top(a) < await top(c) && await top(c) < await top(b), 'Expanded rows show the persisted A,C,B order');
    await page.reload(); await ready();
    await expectOrder([a, c, b]); await setTree(true);
    assert.ok(await top(a) < await top(c) && await top(c) < await top(b));
    await capture('timeline-order-expanded-ACB.png');
    const cHandle = row(c).locator('[data-task-reorder-handle]');
    await cHandle.focus(); await page.keyboard.press('Alt+ArrowDown');
    await expectOrder([a, b, c]);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await expectOrder([a, c, b]);
    assert.deepEqual(await geometryState(c), before);
    const handleBox = await cHandle.boundingBox(), handleTarget = await row(b).boundingBox();
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2); await page.mouse.down();
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleTarget.y + 47, {steps: 10});
    assert.equal(await page.locator('.timeline-order-drop-line:not(.is-invalid)').count(), 1);
    await page.mouse.up(); await expectOrder([a, b, c]);
    assert.deepEqual(await geometryState(c), before, 'Dragging a tree reorder handle changes no date or Group');
    assert.equal(await page.locator('#timeline-task-data').evaluate((script, id) => JSON.parse(script.textContent).find(task => task.id === id).parent, c), '');
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await expectOrder([a, c, b]);
    console.log('PASS exact A/B/C nearest-preceding lanes, vertical join, saved order, reload, mouse/Alt-arrow handles and Ctrl+Z');

    await setTree(false);
    const writesBeforeReject = writes.length;
    await dropBarAtRow(b, a, true);
    assert.equal(writes.length, writesBeforeReject, 'Collision rejection must not POST a changed order or schedule');
    await expectOrder([a, c, b]);
    const dateBox = await bar(c).boundingBox();
    const dayWidth = await page.locator('[data-timeline-scroll]').evaluate(viewport => Number.parseFloat(getComputedStyle(viewport).getPropertyValue('--day-width')));
    const writesBeforeDates = writes.length;
    await page.mouse.move(dateBox.x + dateBox.width / 2, dateBox.y + dateBox.height / 2); await page.mouse.down();
    await page.mouse.move(dateBox.x + dateBox.width / 2 + dayWidth * 2, dateBox.y + dateBox.height / 2 + 12, {steps: 10}); await page.mouse.up();
    await page.waitForFunction(({id, start}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start === start, {id: c, start: day(9)});
    await ready();
    assert.deepEqual(writes.slice(writesBeforeDates), [`/tasks/timeline/tasks/${c}/`], 'Horizontal drag with small vertical jitter is a date edit, not reorder');
    assert.deepEqual(await canonical(), [a, c, b]);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await page.waitForFunction(({id, start}) => document.querySelector(`[data-task-bar][data-task-id="${id}"]`)?.dataset.start === start, {id: c, start: day(7)});
    await ready(); assert.deepEqual(await geometryState(c), before);
    console.log('PASS compact collision drop rejects safely; horizontal date drag with 12px vertical jitter is not a reorder');

    await marker('C flag blocks joining A', c, 1);
    await visit(); await ready(); await setTree(false);
    assert.notEqual(await top(a), await top(c), 'A distant C milestone prevents joining A despite disjoint task bars');
    const writesBeforeFlagReject = writes.length;
    await dropBarAtRow(c, a, true);
    assert.equal(writes.length, writesBeforeFlagReject, 'A milestone-only collision must reject without a server write');
    await expectOrder([a, c, b]);

    const child = await createTask('Child', 10, 11, a), grandchild = await createTask('Grandchild', 12, 13, child);
    const unscheduled = await createTask('Unscheduled child', null, null, a);
    const childFlag = await marker('Child flag', child, 11), grandFlag = await marker('Grandchild flag', grandchild, 13);
    const unscheduledFlag = await marker('Unscheduled child flag', unscheduled, 15), ownFlag = await marker('Parent own flag', a, 0);
    await visit(); await ready(); await setTree(true);
    await expectOrder([a, child, grandchild, c, b]);
    assert.equal(await row(a).locator('[data-toggle-task]').count(), 1);
    assert.equal(await row(child).locator('[data-toggle-task]').count(), 1);
    const groupToggle = page.locator(`[data-timeline-section][data-group="${groupId}"] [data-group-toggle]`);
    const parentToggle = row(a).locator('[data-toggle-task]');
    const groupDisclosure = await disclosureState(groupToggle), parentDisclosure = await disclosureState(parentToggle);
    for (const state of [groupDisclosure, parentDisclosure]) {
      assert.equal(state.expanded, 'true'); assert.equal(state.text, '', 'Disclosure uses a drawn icon, not a font glyph');
      assert.equal(state.width, 18); assert.equal(state.height, 18);
      assert.ok(Math.abs(state.offsetY) <= 1, 'Chevron is vertically centered in its button');
      assert.ok(state.rotation > 0, 'Expanded Group and Task chevrons both point down');
      assert.notEqual(state.content, 'none');
    }
    assert.equal(groupDisclosure.color, parentDisclosure.color, 'Group and Task chevrons share the same muted color');
    await groupToggle.click();
    assert.equal((await disclosureState(groupToggle)).expanded, 'false');
    assert.ok((await disclosureState(groupToggle)).rotation < 0, 'Collapsed Group chevron points right');
    assert.equal(await row(a).isVisible(), false);
    await groupToggle.press('Enter');
    assert.equal(await row(a).isVisible(), true);
    assert.equal(await row(a).locator('.timeline-task-label-name').evaluate(element => getComputedStyle(element).fontWeight), '700');
    assert.ok(await row(grandchild).locator('.timeline-tree-column.is-branch').count());
    assert.ok(await row(grandchild).locator('.timeline-tree-column.is-through').count(), 'Grandchild has a true vertical ancestor connector');
    for (const id of [childFlag, grandFlag, unscheduledFlag, ownFlag]) assert.equal(await page.locator(`[data-milestone-id="${id}"]`).count(), 1);
    const body = row(a).locator('..');
    const expandedHeight = await body.evaluate(element => element.getBoundingClientRect().height);
    await row(a).locator('[data-toggle-task]').click();
    assert.equal((await disclosureState(parentToggle)).expanded, 'false');
    assert.ok((await disclosureState(parentToggle)).rotation < 0, 'Collapsed parent chevron matches the Group');
    assert.equal(await row(child).isVisible(), false); assert.equal(await row(grandchild).isVisible(), false);
    for (const id of [childFlag, grandFlag, unscheduledFlag]) assert.equal(await page.locator(`[data-milestone-id="${id}"]`).count(), 0);
    assert.equal(await page.locator(`[data-milestone-id="${ownFlag}"]`).count(), 1);
    assert.equal(expandedHeight - await body.evaluate(element => element.getBoundingClientRect().height), 104, 'Collapsed descendants leave no blank rows');
    const movedParentBefore = await geometryState(a), childBefore = await geometryState(child);
    await row(a).locator('[data-task-reorder-handle]').focus(); await page.keyboard.press('Alt+ArrowDown');
    await expectOrder([c, a, child, grandchild, b]);
    assert.equal(await row(child).isVisible(), false, 'Moving a parent block preserves collapse state');
    assert.deepEqual(await geometryState(a), movedParentBefore); assert.deepEqual(await geometryState(child), childBefore);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await expectOrder([a, child, grandchild, c, b]);
    await row(a).locator('[data-toggle-task]').click();
    assert.equal(await row(child).isVisible(), true); assert.equal(await row(grandchild).isVisible(), true);
    for (const id of [childFlag, grandFlag, unscheduledFlag]) assert.equal(await page.locator(`[data-milestone-id="${id}"]`).count(), 1);
    await capture('timeline-order-parent-branches.png');
    assert.equal((await page.locator('body').innerText()).includes('QA private secret'), false);
    assert.deepEqual(errors, []);
    console.log('PASS true connectors/bold parent arrows, scheduled and unscheduled descendant flags, no-gap collapse and whole-subtree reorder Undo');

    const parentId = id => page.locator('#timeline-task-data').evaluate((script, id) => JSON.parse(script.textContent).find(task => task.id === id).parent, id);
    const dragTree = async (source, target, offset = 26, invalid = false, useBar = false) => {
      const handle = useBar ? bar(source) : row(source).locator('[data-task-reorder-handle]');
      await handle.focus(); const from = await handle.boundingBox();
      const to = target === 'group' ? await groupToggle.boundingBox() : await row(target).locator('.timeline-task-label').boundingBox();
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2); await page.mouse.down();
      await page.mouse.move(to.x + Math.min(to.width / 2, 90), to.y + (target === 'group' ? to.height / 2 : offset), {steps: 12});
      assert.equal(await page.locator(`${offset >= 13 && offset <= 39 || target === 'group' ? '.timeline-parent-drop-target' : '.timeline-order-drop-line'}${invalid ? '.is-invalid' : ':not(.is-invalid)'}`).count(), 1);
      await page.mouse.up(); await ready();
    };
    const cDates = await geometryState(c);
    await row(a).locator('[data-toggle-task]').click();
    await dragTree(c, a);
    await page.waitForFunction(({id, parent}) => JSON.parse(document.querySelector('#timeline-task-data').textContent).find(task => task.id === id)?.parent === parent, {id: c, parent: a});
    await ready();
    assert.equal(await parentId(c), a); assert.deepEqual(await geometryState(c), cDates);
    assert.equal(await row(child).isVisible(), true, 'Dropping inside a collapsed parent expands it');
    assert.equal(await row(c).evaluate(element => getComputedStyle(element).getPropertyValue('--task-depth').trim()), '1');
    await page.reload(); await ready(); assert.equal(await parentId(c), a);
    await dragTree(c, b); assert.equal(await parentId(c), b, 'Move between parents by center drop');
    assert.deepEqual(await geometryState(c), cDates);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z');
    await ready(); assert.equal(await parentId(c), a, 'Undo restores the former parent and tree order');
    await dragTree(c, b, 26, false, true);
    assert.equal(await parentId(c), b, 'Dragging the actual calendar bar into the tree also changes parent');
    assert.deepEqual(await geometryState(c), cDates, 'Calendar-to-tree drag must not shift dates despite horizontal pointer movement');
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z'); await ready(); assert.equal(await parentId(c), a);
    const writesBeforeCycle = writes.length;
    await dragTree(a, grandchild, 26, true);
    assert.equal(writes.length, writesBeforeCycle, 'Cannot drop a parent into its descendant');
    assert.equal(await parentId(a), '');
    await dragTree(child, b);
    assert.equal(await parentId(child), b); assert.equal(await parentId(grandchild), child, 'Moving a parent keeps its descendants attached');
    assert.equal(await page.locator(`[data-milestone-id="${grandFlag}"]`).count(), 1);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z'); await ready();
    assert.equal(await parentId(child), a);
    await dragTree(c, 'group'); assert.equal(await parentId(c), '', 'Group header removes the parent without changing Group');
    assert.deepEqual(await geometryState(c), cDates);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z'); await ready(); assert.equal(await parentId(c), a);
    await dragTree(c, b, 47); assert.equal(await parentId(c), '', 'Bottom edge adopts the target sibling parent');
    await expectOrder([a, child, grandchild, b, c]);
    await page.locator('[data-timeline-scroll]').focus(); await page.keyboard.press('Control+z'); await ready(); assert.equal(await parentId(c), a);
    const writesBeforeEscape = writes.length, from = await row(c).locator('[data-task-reorder-handle]').boundingBox(), to = await row(b).locator('.timeline-task-label').boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2); await page.mouse.down();
    await page.mouse.move(to.x + 90, to.y + 26, {steps: 10}); await page.keyboard.press('Escape'); await page.mouse.up();
    await ready(); assert.equal(writes.length, writesBeforeEscape); assert.equal(await parentId(c), a);
    await capture('timeline-parent-drag.png');
    assert.deepEqual(errors, []);
    console.log('PASS tree center/edge/Group-header reparenting, nested subtree/flags, collapsed target, reload, Undo, cycle rejection and Escape; dates/Group unchanged');
  } catch (error) {
    error.message += `\nQA writes: ${JSON.stringify(writes.slice(-8))}; JS errors: ${JSON.stringify(errors)}`;
    throw error;
  } finally { await browser.close(); }
});
