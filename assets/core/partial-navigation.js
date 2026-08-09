(() => {
  const requests = new Map();

  const announce = message => {
    let region = document.querySelector('[data-partial-announcer]');
    if (!region) {
      region = document.createElement('div');
      region.className = 'sr-only';
      region.dataset.partialAnnouncer = '';
      region.setAttribute('role', 'status');
      region.setAttribute('aria-live', 'polite');
      document.body.append(region);
    }
    region.textContent = '';
    window.requestAnimationFrame(() => { region.textContent = message; });
  };

  const pageUrl = value => {
    try {
      const url = new URL(value, window.location.href);
      return url.origin === window.location.origin ? url : null;
    } catch (_) {
      return null;
    }
  };

  const responseRoot = (html, targetSelector) => {
    const documentFragment = new DOMParser().parseFromString(html, 'text/html');
    return documentFragment.querySelector(targetSelector);
  };

  const partialRequest = async (url, options) => {
    const target = document.querySelector(options.target);
    const targetUrl = pageUrl(url);
    if (!target || !targetUrl) return false;

    const requestMethod = (options.method || 'GET').toUpperCase();
    const activeRequest = requests.get(options.target);
    if (activeRequest?.method === 'GET') {
      activeRequest.controller.abort();
    } else if (activeRequest) {
      announce('Waiting for the current update to finish');
      await activeRequest.completion;
      return partialRequest(url, options);
    }
    const controller = new AbortController();
    let finishRequest;
    const completion = new Promise(resolve => { finishRequest = resolve; });
    const requestState = {controller, method: requestMethod, completion};
    requests.set(options.target, requestState);
    const oldTop = target.getBoundingClientRect().top;
    target.classList.add('is-partial-loading');
    target.setAttribute('aria-busy', 'true');
    announce('Loading updated content');

    try {
      const response = await fetch(targetUrl.href, {
        method: requestMethod,
        body: options.body,
        credentials: 'same-origin',
        signal: controller.signal,
        headers: {
          'Accept': 'text/html',
          'X-Requested-With': 'XMLHttpRequest',
          'X-MMP-Partial': options.partial,
        },
      });
      const contentType = response.headers.get('Content-Type') || '';
      if (!response.ok || !contentType.includes('text/html')) throw new Error('Partial page unavailable');

      const replacement = responseRoot(await response.text(), options.target);
      if (!replacement) throw new Error('Partial content missing');
      const imported = document.importNode(replacement, true);
      target.replaceWith(imported);
      const newTop = imported.getBoundingClientRect().top;
      window.scrollBy(0, newTop - oldTop);
      document.dispatchEvent(new CustomEvent('mmp:page-loaded', {detail: {root: imported}}));
      announce('Content updated');
      if (options.focus) {
        const focusTarget = imported.querySelector('[data-partial-focus], h1, h2') || imported;
        if (!focusTarget.hasAttribute('tabindex')) focusTarget.setAttribute('tabindex', '-1');
        window.requestAnimationFrame(() => focusTarget.focus({preventScroll: true}));
      }

      const finalUrl = response.url || targetUrl.href;
      if (options.history === 'push') {
        window.history.pushState({mmpPartial: true, target: options.target, partial: options.partial}, '', finalUrl);
      } else if (options.history === 'replace') {
        window.history.replaceState({mmpPartial: true, target: options.target, partial: options.partial}, '', finalUrl);
      }
      return true;
    } catch (error) {
      if (error.name === 'AbortError') return true;
      announce('Unable to update content');
      return false;
    } finally {
      const isCurrentRequest = requests.get(options.target) === requestState;
      if (isCurrentRequest) requests.delete(options.target);
      finishRequest();
      if (isCurrentRequest && target.isConnected) {
        target.classList.remove('is-partial-loading');
        target.setAttribute('aria-busy', 'false');
      }
    }
  };

  const rememberCurrentPartial = (target, partial) => {
    if (window.history.state?.mmpPartial) return;
    window.history.replaceState({mmpPartial: true, target, partial}, '', window.location.href);
  };

  document.addEventListener('click', async event => {
    const link = event.target.closest('a[data-partial-nav]');
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = link.dataset.partialTarget;
    const partial = link.dataset.partialName;
    if (!target || !partial) return;

    event.preventDefault();
    rememberCurrentPartial(target, partial);
    const handled = await partialRequest(link.href, {target, partial, history: 'push', focus: true});
    if (!handled) window.location.assign(link.href);
  });

  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-partial-nav]');
    if (!form || event.defaultPrevented) return;
    const target = form.dataset.partialTarget;
    const partial = form.dataset.partialName;
    const action = pageUrl(form.action || window.location.href);
    if (!target || !partial || !action) return;

    event.preventDefault();
    const submitter = event.submitter;
    const method = (submitter?.formMethod || form.method || 'GET').toUpperCase();
    const data = submitter ? new FormData(form, submitter) : new FormData(form);
    let requestUrl = action;
    let body;

    if (method === 'GET') {
      const params = new URLSearchParams();
      data.forEach((value, key) => {
        if (!(value instanceof File)) params.append(key, value);
      });
      requestUrl = new URL(action.href);
      requestUrl.search = params.toString();
      requestUrl.hash = '';
    } else {
      body = data;
    }

    rememberCurrentPartial(target, partial);
    if (submitter) submitter.disabled = true;
    const handled = await partialRequest(requestUrl.href, {
      method,
      body,
      target,
      partial,
      history: method === 'GET' ? 'push' : 'replace',
      focus: method === 'GET',
    });
    if (submitter?.isConnected) submitter.disabled = false;
    if (!handled) {
      if (method === 'GET') window.location.assign(requestUrl.href);
      else window.location.reload();
    }
  });

  window.addEventListener('popstate', async event => {
    const state = event.state;
    if (!state?.mmpPartial || !document.querySelector(state.target)) return;
    const handled = await partialRequest(window.location.href, {
      target: state.target,
      partial: state.partial,
      history: 'none',
      focus: true,
    });
    if (!handled) window.location.reload();
  });
})();
