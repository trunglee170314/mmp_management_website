const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('./partial-navigation.js'), 'utf8');

const submit = async ({overrideAction, overrideMethod} = {}) => {
  const listeners = {};
  const calls = [];
  const region = {textContent: ''};
  const target = {
    classList: {add() {}, remove() {}}, setAttribute() {},
    getBoundingClientRect: () => ({top: 30}), replaceWith() {}, isConnected: false,
  };
  const form = {
    // Simulate DOM clobbering by <input name="action"> / name="method".
    action: {value: 'add'}, method: {value: 'unrelated'},
    dataset: {partialTarget: '#board-workspace', partialName: 'board-workspace'},
    getAttribute: name => ({action: '/boards/42/users/', method: 'post'}[name] ?? null),
    closest: () => form,
  };
  const button = {
    disabled: false, isConnected: false,
    getAttribute: name => ({formaction: overrideAction, formmethod: overrideMethod}[name] ?? null),
  };
  const window = {
    location: {href: 'http://example.test/boards/', origin: 'http://example.test'},
    history: {state: {mmpPartial: true}, replaceState() {}, pushState() {}},
    addEventListener() {}, requestAnimationFrame: callback => callback(), scrollBy() {},
  };
  const document = {
    addEventListener: (name, callback) => { listeners[name] = callback; },
    querySelector: selector => selector === '#board-workspace' ? target : region,
    importNode: node => node, dispatchEvent() {},
  };
  class FormData {
    constructor() { this.fields = {action: 'add', user_id: '7'}; }
    forEach(callback) { Object.entries(this.fields).forEach(([key, value]) => callback(value, key)); }
  }
  vm.runInNewContext(source, {
    window, document, URL, FormData, File: class {}, AbortController,
    CustomEvent: class {},
    DOMParser: class { parseFromString() { return {querySelector: () => target}; } },
    fetch: async (url, options) => {
      calls.push({url, options});
      return {ok: true, url: 'http://example.test/boards/',
        headers: {get: () => 'text/html'}, text: async () => '<div id="board-workspace"></div>'};
    },
  });
  await listeners.submit({target: form, submitter: button, defaultPrevented: false, preventDefault() {}});
  return calls;
};

test('Board + submits to the form URL despite a hidden input named action', async () => {
  const [request] = await submit();
  assert.equal(request.url, 'http://example.test/boards/42/users/');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.body.fields.action, 'add');
  assert.equal(request.options.body.fields.user_id, '7');
});

test('partial forms respect explicit submit-button action and method overrides', async () => {
  const [request] = await submit({overrideAction: '/boards/43/users/', overrideMethod: 'POST'});
  assert.equal(request.url, 'http://example.test/boards/43/users/');
  assert.equal(request.options.method, 'POST');
});
