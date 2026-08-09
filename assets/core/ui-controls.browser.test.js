// Opt-in standalone browser fixture: no database, running app or credentials.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Shared Task picker: internal search, keyboard, refresh, reset and read-only state', {
  skip: process.env.MMP_BROWSER_TESTS !== '1', timeout: 60000,
}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1500, height: 950}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const markup = fs.readFileSync(path.join(__dirname, '../../templates/tasks/_milestone_task_picker.html'), 'utf8')
      .replace('{{ milestone_form.task }}', '<select name="task" id="id_task"><option value="">---------</option><option value="1">[Qt6] Render benchmark graphics</option><option value="2">Codec driver acceleration</option><option value="3" disabled>Archived result</option></select>');
    await page.setContent(`<form><label for="milestone-task-picker-toggle">Task</label>${markup}<button id="outside" type="button">Outside</button></form>`);
    await page.addStyleTag({content: fs.readFileSync(path.join(__dirname, 'task-filters.css'), 'utf8')});
    const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
    const begin = source.indexOf('const initializeSingleTaskPickers =');
    const end = source.indexOf('\ninitializeSingleTaskPickers();', begin);
    await page.addScriptTag({content: source.slice(begin, end) + '\ninitializeSingleTaskPickers();'});
    const toggle = page.locator('#milestone-task-picker-toggle');
    const search = page.locator('[data-single-task-search]');
    assert.equal(await toggle.textContent().then(text => text.trim()), 'No task');
    assert.equal(await page.locator('select[name="task"]').isVisible(), false);
    assert.equal(await search.isVisible(), false, 'Search belongs inside the closed dropdown');
    await toggle.click(); await search.waitFor();
    await page.waitForFunction(() => document.activeElement.matches('[data-single-task-search]'));
    await search.fill('GRAPHICS render');
    assert.equal(await page.locator('.single-task-option:visible').count(), 1);
    await search.press('ArrowDown'); await page.keyboard.press('Enter');
    assert.equal(await page.locator('select[name="task"]').inputValue(), '1');
    assert.match(await toggle.textContent(), /Render benchmark/);
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    await toggle.press('ArrowDown'); await search.fill('not present anywhere');
    assert.equal(await page.locator('.board-picker-no-results').isVisible(), true);
    await search.press('Escape');
    assert.equal(await search.isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'milestone-task-picker-toggle');

    await page.evaluate(() => {
      const select = document.querySelector('select[name="task"]');
      select.add(new Option('Current legacy Task with full searchable title', '99'));
      select.value = '99';
      document.querySelector('[data-single-task-picker]').refreshSingleTaskPicker();
    });
    assert.match(await toggle.textContent(), /Current legacy Task/);
    await toggle.click(); await search.fill('legacy searchable'); await search.press('Enter');
    assert.equal(await page.locator('select[name="task"]').inputValue(), '99');
    await page.evaluate(() => { document.querySelector('select[name="task"]').disabled = true; });
    await page.waitForFunction(() => document.querySelector('#milestone-task-picker-toggle').disabled);
    assert.equal(await toggle.isEnabled(), false);
    await page.evaluate(() => { const select = document.querySelector('select[name="task"]'); select.disabled = false; select.value = '2'; select.dispatchEvent(new Event('change')); });
    await page.waitForFunction(() => !document.querySelector('#milestone-task-picker-toggle').disabled);
    assert.match(await toggle.textContent(), /Codec driver/);
    await page.evaluate(() => document.querySelector('form').reset());
    await page.waitForFunction(() => document.querySelector('.board-picker-value').textContent === 'No task');
    await toggle.click(); await search.fill('codec'); await search.press('Enter');
    assert.equal(await page.locator('select[name="task"]').inputValue(), '2');
    await toggle.click(); await search.fill('driver'); await search.press('Home');
    assert.equal(await page.evaluate(() => document.activeElement.matches('[data-single-task-search]')), true, 'Home/End must retain native caret editing in the search field');
    await search.press('Tab');
    await page.waitForFunction(() => document.querySelector('#milestone-task-picker-options').hidden);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'outside', 'Tab should leave the picker instead of traversing every Task');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('Workspace uses 1920/2560px widths with both sidebar states; forms stay readable', {
  skip: process.env.MMP_BROWSER_TESTS !== '1', timeout: 60000,
}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const page = await browser.newPage();
    await page.setContent('<aside class="sidebar"></aside><main class="app-main"><header class="page-header"><h1>Workspace</h1></header><section class="panel table-panel"><table><thead><tr><th>Task</th></tr></thead><tbody><tr><td>Example</td></tr></tbody></table></section><section class="form-panel panel task-form">Readable form</section></main>');
    for (const file of ['styles.css', 'task-filters.css', 'interaction-enhancements.css']) await page.addStyleTag({content: fs.readFileSync(path.join(__dirname, file), 'utf8')});
    const originalFont = await page.locator('.app-main').evaluate(main => getComputedStyle(main).fontSize);
    const settledWidth = (left, right) => page.waitForFunction(({left, right}) => {
      const main = document.querySelector('.app-main'), rect = main.getBoundingClientRect();
      return rect.left === left && rect.right === right && getComputedStyle(main).marginLeft === `${left}px`;
    }, {left, right});
    for (const width of [1920, 2560]) {
      await page.setViewportSize({width, height: 1080});
      for (const collapsed of [false, true]) {
        await page.evaluate(value => document.body.classList.toggle('sidebar-is-collapsed', value), collapsed);
        await settledWidth(collapsed ? 0 : 236, width);
        const result = await page.locator('.app-main').evaluate(main => ({left: main.getBoundingClientRect().left, right: main.getBoundingClientRect().right, max: getComputedStyle(main).maxWidth, font: getComputedStyle(main).fontSize}));
        assert.equal(result.left, collapsed ? 0 : 236);
        assert.equal(result.right, width, `Content should use all available width at ${width}px`);
        assert.equal(result.max, 'none'); assert.equal(result.font, originalFont);
        assert.ok((await page.locator('.form-panel').boundingBox()).width <= 790);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      }
    }
    await page.emulateMedia({media: 'print'});
    await settledWidth(0, 2560);
    assert.equal(await page.locator('.app-main').evaluate(main => getComputedStyle(main).marginLeft), '0px');
    await page.emulateMedia({media: 'screen'}); await page.setViewportSize({width: 390, height: 844});
    await settledWidth(0, 390);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await page.locator('.app-main').evaluate(main => main.getBoundingClientRect().left), 0);
  } finally { await browser.close(); }
});
