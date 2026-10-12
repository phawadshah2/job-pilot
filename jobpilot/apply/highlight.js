// Marks fields the human still has to handle and shows a banner. Never submits.
(args) => {
  const { ids, filled } = args;
  const title = String(args.title).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
  const style = document.getElementById('jp-style') || Object.assign(document.createElement('style'), { id: 'jp-style' });
  style.textContent = `
    [data-jp-todo] { outline: 3px solid #e53935 !important; outline-offset: 2px; }
    [data-jp-done] { outline: 2px solid #43a047 !important; outline-offset: 1px; }
    #jp-banner { position: fixed; z-index: 2147483647; left: 12px; right: 12px; bottom: 12px;
      background: #1a237e; color: #fff; font: 14px/1.4 -apple-system, sans-serif; padding: 10px 14px;
      border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.3); }
    #jp-banner b { color: #ffeb3b; }
    #jp-banner button { float: right; background: none; border: 1px solid #fff; color: #fff; border-radius: 4px; cursor: pointer; }`;
  document.head.appendChild(style);
  ids.todo.forEach(id => { const el = document.querySelector(`[data-jp-id="${id}"]`); if (el) el.setAttribute('data-jp-todo', ''); });
  ids.done.forEach(id => { const el = document.querySelector(`[data-jp-id="${id}"]`); if (el) el.setAttribute('data-jp-done', ''); });
  if (window.top !== window) return;
  let b = document.getElementById('jp-banner');
  if (!b) { b = document.createElement('div'); b.id = 'jp-banner'; document.body.appendChild(b); }
  b.innerHTML = `<button onclick="this.parentNode.remove()">hide</button>
    <b>job-pilot</b>: ${title}. Filled <b>${filled}</b> field(s) (green) and attached the tailored CV.
    Answer the <b>red</b> fields, review everything, then click <b>Submit</b> yourself.
    Afterwards, return to the terminal.`;
}
