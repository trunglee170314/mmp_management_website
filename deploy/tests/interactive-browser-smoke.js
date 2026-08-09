/* Run against interactive-fixture-server.py ONLY, never a production URL. */
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const base = 'http://127.0.0.1:18081';
  assert(process.env.QA_SESSION, 'QA_SESSION from the disposable fixture container is required');
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  await context.addCookies([{name: 'sessionid', value: process.env.QA_SESSION, url: base}]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const expectVisible = async locator => { await locator.waitFor({state: 'visible'}); };
  const saved = async action => {
    const response = page.waitForResponse(response => response.request().method() === 'POST' && !response.url().includes('/profile/'));
    await action();
    const result = await response;
    assert(result.ok(), `Mutation failed ${result.status()}: ${await result.text()}`);
    return result;
  };
  try {
    await page.goto(`${base}/tasks/timeline/`);
    await expectVisible(page.locator('[data-timeline-scroll]'));
    assert.equal(await page.locator('body').innerText().then(text => text.includes('QA private secret')), false);
    assert.equal(await page.locator('[data-milestone-id]').count(), 2);
    await expectVisible(page.locator('[data-milestone-id].is-important').or(page.locator('[data-milestone-id][data-important="true"]')).first());
    const bar = page.locator('[data-task-bar]').first();
    const before = await bar.getAttribute('data-start');
    await bar.scrollIntoViewIfNeeded();
    const box = await bar.boundingBox();
    const width = await page.locator('[data-timeline-scroll]').evaluate(node => parseFloat(getComputedStyle(node).getPropertyValue('--day-width')));
    await saved(async () => {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + width * 2, box.y + box.height / 2, {steps: 8});
      await page.mouse.up();
    });
    await page.waitForFunction(previous => document.querySelector('[data-task-bar]')?.dataset.start !== previous, before);
    console.log('PASS timeline task drag saved and rerendered');
    await saved(() => page.keyboard.press('Control+z'));
    await page.waitForFunction(previous => document.querySelector('[data-task-bar]')?.dataset.start === previous, before);
    console.log('PASS timeline Undo uses fresh revision');

    for (const edge of ['start', 'due']) {
      const selector = `[data-task-bar] [data-resize="${edge}"]`;
      const handle = page.locator(selector).first();
      await handle.scrollIntoViewIfNeeded();
      const point = await handle.boundingBox();
      const previous = await bar.getAttribute(`data-${edge}`);
      await saved(async () => {
        await page.mouse.move(point.x + point.width / 2, point.y + point.height / 2);
        await page.mouse.down();
        await page.mouse.move(point.x + point.width / 2 + width * (edge === 'start' ? -1 : 1), point.y + point.height / 2, {steps: 8});
        await page.mouse.up();
      });
      await page.waitForFunction(({edge, previous}) => document.querySelector('[data-task-bar]')?.dataset[edge] !== previous, {edge, previous});
      await saved(() => page.keyboard.press('Control+z'));
      await page.waitForFunction(({edge, previous}) => document.querySelector('[data-task-bar]')?.dataset[edge] === previous, {edge, previous});
    }
    console.log('PASS task resize at both ends and Undo');

    const flag = page.getByRole('button', {name: /Timeline.*QA small milestone/});
    await flag.scrollIntoViewIfNeeded();
    const flagBox = await flag.boundingBox();
    const markerDate = await flag.getAttribute('data-date');
    await saved(async () => {
      await page.mouse.move(flagBox.x + flagBox.width / 2, flagBox.y + flagBox.height / 2);
      await page.mouse.down();
      await page.mouse.move(flagBox.x + flagBox.width / 2 + width, flagBox.y + flagBox.height / 2, {steps: 8});
      await page.mouse.up();
    });
    await page.waitForFunction(previous => [...document.querySelectorAll('[data-milestone-id]')].find(node => node.getAttribute('aria-label').includes('QA small milestone'))?.dataset.date !== previous, markerDate);
    await saved(() => page.keyboard.press('Control+z'));
    await page.waitForFunction(previous => [...document.querySelectorAll('[data-milestone-id]')].find(node => node.getAttribute('aria-label').includes('QA small milestone'))?.dataset.date === previous, markerDate);
    console.log('PASS milestone drag and Undo');

    const track = page.locator('.timeline-track').last();
    const trackBox = await track.boundingBox();
    const viewportBox = await page.locator('[data-timeline-scroll]').boundingBox();
    await page.mouse.click(viewportBox.x + viewportBox.width - 70, trackBox.y + trackBox.height - 5);
    await expectVisible(page.locator('[data-timeline-create-menu]'));
    await page.locator('[data-create-kind="milestone"]').click();
    await page.locator('[data-milestone-form] [name="name"]').fill('QA created milestone');
    await page.locator('[data-milestone-form] [name="is_important"]').check();
    assert.equal(await page.locator('[data-milestone-form] [data-milestone-color]').count(), 9);
    await saved(() => page.locator('[data-save-milestone]').click());
    await expectVisible(page.getByText('QA created milestone', {exact: false}).first());
    console.log('PASS blank click creates Important milestone');
    if (process.env.QA_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.QA_SCREENSHOT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.QA_SCREENSHOT_DIR, 'timeline-verified.png'), fullPage: true});
    }

    await page.goto(`${base}/system-map/`);
    await expectVisible(page.locator('[data-map-viewport]'));
    await page.locator('[data-node-search]').fill('needle123');
    await page.waitForSelector('.relationship-node.is-search-match');
    assert.equal(await page.locator('.relationship-node.is-search-match').count(), 1);
    await page.locator('.relationship-node.is-search-match').click();
    assert((await page.locator('[data-node-detail-description]').innerText()).includes('needle123'));
    console.log('PASS System Map description search and right details');
    await page.locator('[data-node-search]').fill('');
    const viewport = page.locator('[data-map-viewport]');
    await viewport.focus();
    await page.keyboard.press('n');
    await expectVisible(page.getByRole('textbox', {name: 'Node name', exact: true}));
    await page.getByRole('textbox', {name: 'Node name', exact: true}).fill('QA quick node');
    await saved(() => page.keyboard.press('Enter'));
    await page.waitForFunction(() => [...document.querySelectorAll('.relationship-node')].some(node => node.textContent.includes('QA quick node')));
    console.log('PASS System Map N quick-create');
    await viewport.focus();
    await saved(() => page.keyboard.press('Control+z'));
    await page.waitForFunction(() => ![...document.querySelectorAll('.relationship-node')].some(node => node.textContent.includes('QA quick node')));
    await saved(() => page.keyboard.press('Control+Shift+z'));
    await page.waitForFunction(() => [...document.querySelectorAll('.relationship-node')].some(node => node.textContent.includes('QA quick node')));
    console.log('PASS System Map Undo/Redo');
    await viewport.focus();
    await page.keyboard.press('k');
    await expectVisible(page.locator('[data-shortcut-dialog]'));
    console.log('PASS shortcut settings K');
    await page.keyboard.press('Escape');
    if (process.env.QA_SCREENSHOT_DIR) {
      await page.screenshot({path: path.join(process.env.QA_SCREENSHOT_DIR, 'system-map-verified.png'), fullPage: true});
    }
    assert.deepEqual(errors, [], 'Browser JavaScript errors');
    console.log('PASS interactive browser smoke');
  } catch (error) {
    if (process.env.QA_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.QA_SCREENSHOT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.QA_SCREENSHOT_DIR, 'interactive-failure.png'), fullPage: true});
    }
    console.error('Browser errors:', errors);
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
