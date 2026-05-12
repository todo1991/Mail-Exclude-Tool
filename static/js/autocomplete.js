// Reusable suggestion dropdown for <input type="email"> or <textarea>.
// Config:
//   taId      - id of the input/textarea
//   ddId      - id of an absolutely-positioned dropdown div inside a relative wrapper
//   warnId    - (optional) id of a warning div for "not in source" entries
//   source    - array of values to suggest from
//   validate  - (optional) function (s)->bool; only orphans where validate(s)===true are flagged

window.setupAutoSuggest = function (cfg) {
  const ta = document.getElementById(cfg.taId);
  const dd = document.getElementById(cfg.ddId);
  const warn = cfg.warnId ? document.getElementById(cfg.warnId) : null;
  if (!ta || !dd) return;

  const sourceArr = cfg.source || [];
  const sourceSet = new Set(sourceArr);
  const validate = cfg.validate || (() => true);
  const isInput = ta.tagName === "INPUT";

  let matches = [];
  let active = -1;

  function currentLine() {
    if (isInput) {
      return { start: 0, end: ta.value.length, text: ta.value.trim() };
    }
    const pos = ta.selectionStart;
    const v = ta.value;
    const start = v.lastIndexOf("\n", pos - 1) + 1;
    const endNl = v.indexOf("\n", pos);
    const end = endNl === -1 ? v.length : endNl;
    return { start, end, text: v.substring(start, end).trim() };
  }

  function existingSet() {
    return new Set(
      ta.value.split(/[\n,;\s]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)
    );
  }

  function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  function updateSuggest() {
    const line = currentLine();
    const q = line.text.toLowerCase();
    if (q.length < 1 || q.includes(" ")) { hide(); return; }
    const taken = existingSet();
    taken.delete(q);
    const m = [];
    for (const e of sourceArr) {
      if (m.length >= 50) break;
      if (e.includes(q) && !taken.has(e)) m.push(e);
    }
    m.sort((a, b) => {
      const ai = a.startsWith(q) ? 0 : 1;
      const bi = b.startsWith(q) ? 0 : 1;
      return ai !== bi ? ai - bi : a.localeCompare(b);
    });
    matches = m.slice(0, 10);
    if (!matches.length) { hide(); return; }
    active = 0;
    render();
    dd.classList.remove("hidden");
  }

  function render() {
    const q = currentLine().text.toLowerCase();
    dd.innerHTML = matches.map((e, i) => {
      const idx = e.toLowerCase().indexOf(q);
      const safe = escHtml(e);
      let html = safe;
      if (idx >= 0 && q) {
        html = safe.substring(0, idx) +
               '<mark class="bg-amber-200 text-slate-900 rounded px-0.5">' +
               safe.substring(idx, idx + q.length) +
               "</mark>" + safe.substring(idx + q.length);
      }
      return `<div data-i="${i}" class="px-3 py-1.5 text-sm font-mono cursor-pointer ${i === active ? "bg-indigo-100" : "hover:bg-slate-50"}">${html}</div>`;
    }).join("");
    dd.querySelectorAll("[data-i]").forEach((el) => {
      el.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        select(parseInt(el.dataset.i, 10));
      });
    });
  }

  function hide() {
    dd.classList.add("hidden");
    matches = []; active = -1;
  }

  function select(i) {
    const v = matches[i];
    if (!v) return;
    if (isInput) {
      ta.value = v;
      hide();
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      ta.focus();
      return;
    }
    const line = currentLine();
    const before = ta.value.substring(0, line.start);
    const after = ta.value.substring(line.end);
    const insertNewline = !after.startsWith("\n");
    ta.value = before + v + (insertNewline ? "\n" : "") + after;
    const newPos = before.length + v.length + 1;
    ta.selectionStart = ta.selectionEnd = newPos;
    hide();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
  }

  function checkOrphans() {
    if (!warn) return;
    const lines = ta.value.split(/[\n,;\s]+/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    const orphans = [];
    for (const v of lines) {
      if (validate(v) && !sourceSet.has(v)) orphans.push(v);
    }
    if (orphans.length === 0) {
      warn.classList.add("hidden");
      warn.innerHTML = "";
    } else {
      warn.classList.remove("hidden");
      warn.innerHTML =
        "⚠ Không có trong list nguồn (không cần exclude): " +
        orphans
          .map((o) => `<code class="bg-amber-100 px-1 rounded mr-1">${escHtml(o)}</code>`)
          .join("");
    }
  }

  function onInput() { updateSuggest(); checkOrphans(); }

  ta.addEventListener("input", onInput);
  ta.addEventListener("focus", updateSuggest);
  ta.addEventListener("blur", () => setTimeout(hide, 150));
  ta.addEventListener("keydown", (e) => {
    if (dd.classList.contains("hidden")) return;
    if (e.key === "ArrowDown") {
      e.preventDefault(); active = Math.min(active + 1, matches.length - 1); render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); active = Math.max(active - 1, 0); render();
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (active >= 0) { e.preventDefault(); select(active); }
    } else if (e.key === "Escape") {
      e.preventDefault(); hide();
    }
  });
};
