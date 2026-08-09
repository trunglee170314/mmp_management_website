// Fake registration in the RAM-only localhost QA fixture; never production.
const test = require('node:test');
const assert = require('node:assert/strict');

test('Internal password policy: registration accepts one digit with no checklist or live password requests', {
  skip: !process.env.MMP_LIVE_QA_URL || !process.env.MMP_LIVE_QA_SESSION,
  timeout: 60000,
}, async () => {
  const base = new URL(process.env.MMP_LIVE_QA_URL);
  assert.ok(['localhost', '127.0.0.1'].includes(base.hostname) && ['18081', '18082', '18083'].includes(base.port));
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 960}});
    await context.addCookies([{name: 'sessionid', value: process.env.MMP_LIVE_QA_SESSION, url: base.origin}]);
    const page = await context.newPage();
    const errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (new URL(request.url()).pathname === '/register/password-check/') checks.push(request.url()); });
    await page.goto(`${base.origin}/system-map/`);
    assert.match(await page.locator('.sidebar-user').innerText(), /QA Owner/);
    assert.equal(await page.locator('#relationship-map-data').evaluate(script => JSON.parse(script.textContent).name), 'QA System Map');
    await context.clearCookies();
    await page.goto(`${base.origin}/register/`);
    assert.equal(await page.locator('#password-rules,[data-rule],[data-password-check-url]').count(), 0);
    assert.equal(await page.locator('#id_password1').getAttribute('minlength'), null);
    await page.locator('#id_display_name').fill('Disposable Password QA');
    await page.locator('#id_username').fill(`qa-password-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
    await page.locator('#id_password1').fill('1');
    await page.locator('#id_password2').fill('1');
    await page.waitForTimeout(300);
    assert.deepEqual(checks, [], 'Typing a password must no longer send live strength-check requests');
    await page.locator('.register-form [type="submit"]').click();
    await page.waitForLoadState('domcontentloaded');
    assert.equal(await page.locator('.register-form').count(), 0, 'One-digit password must submit without a validation error');
    assert.match(await page.locator('main').innerText(), /admin.*approv|approv.*admin/i);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
