// Injected into every frame of an application page. Read-only: tags each
// form control with data-jp-id and returns a description of it, including
// the best human-readable label we can find. Python decides what to fill.
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').replace(/[*✱]/g, '').trim();
  const visible = el => {
    if (el.type === 'file') return true; // file inputs are usually hidden behind a button
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const textOf = id => { const n = id && document.getElementById(id); return n ? clean(n.innerText || n.textContent) : ''; };

  const labelFor = el => {
    const parts = [];
    if (el.labels && el.labels.length) parts.push([...el.labels].map(l => clean(l.innerText)).join(' '));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) parts.push(lb.split(/\s+/).map(textOf).join(' '));
    if (el.getAttribute('aria-label')) parts.push(clean(el.getAttribute('aria-label')));
    if (!parts.join('').trim()) {
      // Walk up to the nearest container that has some label-ish text.
      let n = el.parentElement;
      for (let i = 0; n && i < 5; i++, n = n.parentElement) {
        // Stop once the container wraps other fields: its label isn't ours.
        if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
        const lab = n.querySelector('label, legend, .application-label, [class*="label"], [class*="Label"]');
        if (lab && !lab.contains(el)) { parts.push(clean(lab.innerText)); break; }
      }
    }
    if (el.placeholder) parts.push(clean(el.placeholder));
    return clean(parts.join(' | ')).slice(0, 300);
  };

  const groupLabel = el => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText).slice(0, 300); }
    const rg = el.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      const lb = rg.getAttribute('aria-labelledby');
      if (lb) return clean(lb.split(/\s+/).map(textOf).join(' ')).slice(0, 300);
      if (rg.getAttribute('aria-label')) return clean(rg.getAttribute('aria-label'));
    }
    let n = el.parentElement;
    for (let i = 0; n && i < 6; i++, n = n.parentElement) {
      const lab = n.querySelector(':scope > label, :scope > div > label, :scope > [class*="label"], :scope > p');
      if (lab && !lab.contains(el) && clean(lab.innerText).length > 3) return clean(lab.innerText).slice(0, 300);
    }
    return '';
  };

  const out = [];
  let i = 0;
  document.querySelectorAll('input, textarea, select, [role="combobox"]').forEach(el => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) return;
    if (!visible(el)) return;
    if (!el.dataset.jpId) el.dataset.jpId = `jp${Date.now().toString(36)}${i++}`;
    const d = {
      id: el.dataset.jpId,
      tag: el.tagName.toLowerCase(),
      type,
      role: el.getAttribute('role') || '',
      name: el.name || '',
      domId: el.id || '',
      label: labelFor(el),
      group: (type === 'radio' || type === 'checkbox') ? groupLabel(el) : '',
      required: el.required || el.getAttribute('aria-required') === 'true' ||
        /\*|✱/.test((el.labels && el.labels[0] && el.labels[0].innerText) || ''),
      value: type === 'checkbox' || type === 'radio' ? (el.checked ? 'on' : '') : (el.value || ''),
      accept: el.getAttribute('accept') || '',
    };
    // react-select etc. add an unlabeled, unnamed dummy input for validation: not a real field.
    if (!d.label && !d.name && !d.domId && type !== 'file') return;
    if (el.tagName === 'SELECT') d.options = [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 60);
    out.push(d);
  });
  return out;
}
