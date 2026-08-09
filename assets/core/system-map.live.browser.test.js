// Real Django/full-CSS integration against the disposable RAM-only QA fixture.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

test('System Map live fixture: native mouse actions with full application CSS', {
  skip: !process.env.MMP_LIVE_QA_URL || !process.env.MMP_LIVE_QA_SESSION,
  timeout: 60000,
}, async () => {
  const base = new URL(process.env.MMP_LIVE_QA_URL);
  assert.ok(['localhost', '127.0.0.1'].includes(base.hostname) && ['18081', '18082', '18083'].includes(base.port));
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1500, height: 950}});
    await context.addCookies([{name: 'sessionid', value: process.env.MMP_LIVE_QA_SESSION, url: base.origin}]);
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const errors = []; let writes = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/system-map/api/')) writes++; });
    await page.goto(`${base.origin}/system-map/`);
    assert.match(await page.locator('.sidebar-user').innerText(), /QA Owner/);
    const fixture = await page.locator('#relationship-map-data').evaluate(script => JSON.parse(script.textContent));
    assert.equal(fixture.name, 'QA System Map', 'Never run against production');
    const node = page.locator(`[data-node-id="${fixture.nodes[0].id}"]`);
    await page.locator('[data-map-viewport]').focus(); await page.keyboard.press('Control+a');
    assert.equal(await page.locator('.relationship-node.is-selected').count(), 2);
    await node.click({position: {x: 25, y: 25}});
    assert.equal(await page.locator('[data-node-details]').isVisible(), true, 'Click after selecting a Group must open Node details');
    assert.equal(await page.locator('[data-node-detail-name]').textContent(), fixture.nodes[0].name);
    await page.locator('[data-close-node-details]').click();
    await page.waitForTimeout(550);
    await node.dblclick({position: {x: 25, y: 25}});
    assert.equal(await page.locator('[data-entity-dialog]').evaluate(dialog => dialog.open), true);
    assert.equal(await page.locator('[data-node-fields] [name="name"]').evaluate(input => getComputedStyle(input).fontSize), '14px');
    await page.keyboard.press('Escape');
    const panBefore = await page.locator('[data-map-surface]').getAttribute('style');
    const box = await node.boundingBox(), beforeWrites = writes;
    await page.mouse.move(box.x + 25, box.y + 25); await page.mouse.down({button: 'right'});
    await page.mouse.move(box.x + 100, box.y + 65, {steps: 8}); await page.mouse.up({button: 'right'});
    assert.notEqual(await page.locator('[data-map-surface]').getAttribute('style'), panBefore);
    assert.equal(writes, beforeWrites, 'Camera panning must not mutate the graph');
    assert.equal(await page.locator('[data-entity-context]').isVisible(), false);
    await node.click({button: 'right', position: {x: 25, y: 25}});
    assert.equal(await page.locator('[data-entity-context]').isVisible(), true);
    assert.equal(await page.locator('[data-map-mode],[data-entity-context] [data-map-command="copy"],[data-entity-context] [data-map-command="duplicate"]').count(), 0);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(550); await node.click({position: {x: 25, y: 25}});
    if (process.env.MMP_QA_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.MMP_QA_SCREENSHOT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.MMP_QA_SCREENSHOT_DIR, 'system-map.png'), fullPage: true});
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
