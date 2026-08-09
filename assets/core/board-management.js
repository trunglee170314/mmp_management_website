(() => {
  const form = document.querySelector('[data-board-form]');
  if (!form) return;

  const list = form.querySelector('[data-component-list]');
  const template = document.querySelector('[data-component-template]');
  const totalForms = form.querySelector('[name="components-TOTAL_FORMS"]');

  const removeRow = row => {
    const deleteInput = row.querySelector('[name$="-DELETE"]');
    if (deleteInput) deleteInput.value = 'on';
    row.hidden = true;
    row.querySelectorAll('input:not([name$="-DELETE"])').forEach(input => {
      input.required = false;
    });
  };

  form.addEventListener('click', event => {
    const removeButton = event.target.closest('[data-component-remove]');
    if (removeButton) {
      removeRow(removeButton.closest('[data-component-row]'));
      return;
    }
    if (!event.target.closest('[data-component-add]') || !template || !totalForms) return;
    const index = Number.parseInt(totalForms.value, 10);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index)).trim();
    const row = wrapper.firstElementChild;
    list.appendChild(row);
    totalForms.value = String(index + 1);
    row.querySelector('input:not([type="hidden"])')?.focus();
  });
})();
