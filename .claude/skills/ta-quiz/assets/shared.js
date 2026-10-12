/* 两个页面共用：DOM 小工具 + 代码组件（C 语法高亮、行号、高亮行、复制）。构建时由 build.py 注入。 */
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
}
const put = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false));

/* Blackboard 的姓名常是「郑琪翰(Zheng Qihan) 通识通修」：拆成中文名、英文名、院系，名单里只显示中文名（没有就显示英文名）。 */
function parseName(raw) {
  raw = String(raw || "");
  const en = (raw.match(/[（(]([^）)]*)[）)]/) || [])[1] || "";
  const tokens = raw.replace(/[（(][^）)]*[）)]/g, " ").trim().split(/\s+/).filter(Boolean);
  let dept = "";
  if (tokens.length > 1 && /^[\u4e00-\u9fff]+$/.test(tokens[tokens.length - 1])) dept = tokens.pop();
  const rest = tokens.join(" "), zh = /[\u4e00-\u9fff]/.test(rest);
  return { short: rest, zh: zh ? rest : "", en: zh ? en.trim() : rest, dept };
}

/* —— C 语法高亮：整份文件一次切词，再按行拆开（块注释可以跨行）—— */
const C_KW = new Set("auto break case const continue default do else enum extern for goto if inline register restrict return sizeof static struct switch typedef union volatile while".split(" "));
const C_TYPE = new Set("int long short char float double void unsigned signed size_t ptrdiff_t ssize_t FILE bool _Bool uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t intptr_t uintptr_t NULL EOF true false".split(" "));
const C_TOKEN = /(\/\*[\s\S]*?(?:\*\/|(?![\s\S])))|(\/\/[^\n]*)|("(?:\\.|[^"\\\n])*"?)|('(?:\\.|[^'\\\n])*'?)|(^[ \t]*#[ \t]*\w+(?:[ \t]*<[^>\n]*>)?)|(\b(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)[fFlLuU]*\b)|([A-Za-z_]\w*)|([\s\S])/gm;
function highlightC(code) {
  const lines = [[]];
  const push = (cls, text) => text.split("\n").forEach((part, i) => { if (i) lines.push([]); if (part) lines[lines.length - 1].push([cls, part]); });
  C_TOKEN.lastIndex = 0;
  let m;
  while ((m = C_TOKEN.exec(code)) !== null) {
    if (m[1] !== undefined || m[2] !== undefined) push("com", m[0]);
    else if (m[3] !== undefined || m[4] !== undefined) push("str", m[0]);
    else if (m[5] !== undefined) push("pre", m[0]);
    else if (m[6] !== undefined) push("num", m[0]);
    else if (m[7] !== undefined) {
      const w = m[7];
      push(C_KW.has(w) ? "kw" : C_TYPE.has(w) ? "ty" : /^[ \t]*\(/.test(code.slice(C_TOKEN.lastIndex, C_TOKEN.lastIndex + 8)) ? "fn" : null, w);
    } else push(null, m[0]);
  }
  return lines;
}
function plainLines(code) { return code.split("\n").map(l => (l ? [[null, l]] : [])); }

function copyText(text, btn, idle = "复制") {
  const done = label => { btn.textContent = label; setTimeout(() => (btn.textContent = idle), 1400); };
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => done("已复制"), () => done("请手动选中"));
}

/* 代码组件。o: {name, code, from, to, hits:Set<行号>, marks:{行号:[{text,title,onclick}]}, maxHeight, compact, meta}
   from/to 只显示这个范围（前后用「···」表示省略）；hits 的行高亮；marks 在行首放可点的小圆标。 */
function codeBlock(o) {
  const code = o.code.replace(/\n$/, "");
  const isC = /\.(c|h)$/i.test(o.name);
  const lines = (isC ? highlightC(code) : plainLines(code));
  const total = lines.length;
  const a = Math.max(1, o.from || 1), b = Math.min(total, o.to || total);
  const hits = o.hits || new Set(), marks = o.marks || {};
  const rows = [];
  if (a > 1) rows.push(el("div", { class: "cb-gap" }, "···"));
  for (let n = a; n <= b; n++) {
    rows.push(el("div", { class: "cb-row" + (hits.has(n) ? " hit" : o.dim && hits.size ? " dim" : ""), "data-ref": `${o.name}:${n}` },
      el("span", { class: "cb-mk" }, (marks[n] || []).map(mk => el("button", { class: "cb-badge", type: "button", title: mk.title, onclick: mk.onclick }, mk.text))),
      el("span", { class: "cb-no" }, String(n)),
      el("span", { class: "cb-src" }, lines[n - 1].length ? lines[n - 1].map(([cls, text]) => cls ? el("span", { class: "t-" + cls }, text) : text) : " ")));
  }
  if (b < total) rows.push(el("div", { class: "cb-gap" }, "···"));
  const shown = code.split("\n").slice(a - 1, b).join("\n");
  const copy = el("button", { class: "cb-copy", type: "button", onclick: e => copyText(shown, e.currentTarget) }, "复制");
  const meta = o.meta || (a === 1 && b === total ? `${total} 行` : `第 ${a}–${b} 行 / 共 ${total} 行`);
  const block = el("div", { class: "cb" + (o.compact ? " cb-compact" : "") + (isC ? "" : " cb-plain") },
    el("div", { class: "cb-head" }, el("span", { class: "cb-name" }, o.name), el("span", { class: "cb-meta" }, meta), isC ? el("span", { class: "cb-lang" }, "C") : null, copy),
    el("div", { class: "cb-body" }, el("div", { class: "cb-in" }, rows)));
  if (o.maxHeight) block.style.setProperty("--cb-max", o.maxHeight);
  return block;
}
