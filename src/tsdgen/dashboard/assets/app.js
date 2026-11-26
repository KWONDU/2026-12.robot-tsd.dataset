// 작업상황설명문 대시보드 프런트엔드 (조회 전용).
// 화면 상태는 URL hash 하나가 전부다: #/<실행>/<화면>[/<인자>[/<인자2>]]. 화면 목록과 블록은 서버가 준다.
// 화면 다섯: 개요(결과), 태스크(장면마다 받은 것과 낸 것), 프롬프트, 데이터 구조(JSON 나무), 실행 정보. 태스크 화면만 여기서 직접 그린다.
// 블록의 kind마다 렌더러가 하나씩 있고(RENDERERS), 새 kind는 함수 하나를 추가한다.
// 데이터는 textContent로만 넣는다(innerHTML 금지). 표시명·채점 척도는 서버(/api/meta)가 사전에서 내려 준다.
"use strict";

const TASKS = "tasks";
const FILTER = "filter";
const OVERVIEW = "overview";
const SHORT_CELL = 28;     // 이 글자 수 이하인 표 칸은 줄바꿈하지 않는다
const MAX_STR = 140;       // JSON 나무에서 긴 글은 여기까지만 보이고 전체는 키 설명의 예시 값에 있다
const AUTO_OPEN_MAX = 12;  // 원소가 이보다 많은 묶음은 처음에 접어 둔다
const SVG_NS = "http://www.w3.org/2000/svg";
const OUTPUT_TABS = [["tsd", "설명문"], ["verification", "검수"], ["reasoning", "추론"]];
const ROLE_KEYS = ["required", "key_means", "auxiliary"];
const state = { meta: null, route: {}, tasks: {}, taskDetails: {}, checkedAt: null, jsonDoc: null,
  outputTab: "tsd", promptRole: "generate", taskOrder: [], galleryHash: null };
const $ = (id) => document.getElementById(id);

async function api(path) {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `${response.status} ${path}`);
  return body;
}

// ---- 기본 도구 ------------------------------------------------------------ //
// null·undefined·false 자식은 건너뛴다. DOM의 append는 null을 "null" 글자로 넣기 때문이다.
const kids = (children) => [children].flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
  .map((c) => (c instanceof Node ? c : document.createTextNode(String(c))));
const fill = (node, ...children) => node.replaceChildren(...kids(children));

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  node.append(...kids(children));
  return node;
}

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...children);
  return node;
}

function fmt(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : String(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const two = (n) => String(n).padStart(2, "0");
const hms = (date) => `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
const usd = (v) => (v === null || v === undefined ? "-" : `$${Number(v).toFixed(2)}`);
function duration(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const s = Math.max(0, Math.round(seconds)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}시간 ${m}분` : m ? `${m}분 ${s % 60}초` : `${s}초`;
}

const label = (group, key) => (state.meta.labels[group] || {})[key] || key;
// 파이프라인·데이터 어휘는 영어 원어를 같이 적는다: "시나리오 선별 (sample)". 일반 화면 문구에는 붙이지 않는다.
const term = (group, key) => { const ko = label(group, key); return ko === key ? key : `${ko} (${key})`; };
const en = (ko, english) => `${ko} (${english})`;
// "한국어 (원어)"에서 원어 부분만 작고 흐리게 그린다
function termText(text) {
  const match = /^(.*) \(([^()]+)\)$/.exec(text || "");
  return match ? [match[1], " ", el("span", { class: "en" }, `(${match[2]})`)] : text;
}
const koOnly = (text) => [termText(text)].flat()[0];
const pill = (cls, text, title) => el("span", { class: `pill ${cls}`, title }, text);
const track = (pct, cls = "") => el("div", { class: `track ${cls}` }, el("span", { style: `width: ${Math.max(0, Math.min(pct, 100))}%` }));
const runHref = (path) => `#/${encodeURIComponent(state.route.run)}/${path}`;
const scoreTone = (score, max) => (score >= max ? "pass" : score <= 0 ? "fail" : "warn");
function button(text, onClick, cls = "btn") {
  const node = el("button", { type: "button", class: cls }, text);
  node.addEventListener("click", onClick);
  return node;
}

// ---- 설명 풍선: 키·검수 줄에 마우스를 올리거나 키보드로 고르면 뜬다 ------------------------ //
function showTip(target, content) {
  const tip = $("tip");
  fill(tip, content);
  tip.hidden = false;
  const rect = target.getBoundingClientRect();
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - tip.offsetWidth - 8));
  const below = rect.bottom + 6 + tip.offsetHeight < window.innerHeight;
  tip.style.left = `${left}px`;
  tip.style.top = `${below ? rect.bottom + 6 : Math.max(8, rect.top - tip.offsetHeight - 6)}px`;
}
const hideTip = () => { $("tip").hidden = true; };
function withTip(node, content) {
  node.addEventListener("mouseenter", () => showTip(node, content()));
  node.addEventListener("mouseleave", hideTip);
  node.addEventListener("focus", () => showTip(node, content()));
  node.addEventListener("blur", hideTip);
  return node;
}

// ---- 기본 블록 렌더러 ------------------------------------------------------ //
function renderTable(data) {
  const cellOf = (cell) => { const text = fmt(cell); return el("td", { class: text.length <= SHORT_CELL ? "nw" : null }, text); };
  const head = el("tr", {}, data.columns.map((c) => el("th", {}, c)));
  const rows = data.rows.map((row) => el("tr", {}, row.map(cellOf)));
  const body = rows.length ? rows : [el("tr", {}, el("td", { colspan: data.columns.length }, "없어요"))];
  return el("div", { class: "table-wrap" }, el("table", {}, [el("thead", {}, head), el("tbody", {}, body)]));
}

function renderDonut(items, block) {
  const total = items.reduce((sum, d) => sum + d.value, 0) || 1;
  let at = 0;
  const stops = items.map((d) => { const from = at; at += (d.value / total) * 360; return `var(--${d.tone}) ${from}deg ${at}deg`; });
  const amount = (v) => (block.unit === "usd" ? usd(v) : `${v}건`);
  return el("div", { class: "donut-wrap" }, [
    el("div", { class: "donut", style: `background: conic-gradient(${stops.join(", ")})` }),
    el("div", {}, items.map((d) => el("div", { class: "legend-row" }, [
      el("span", { class: "swatch", style: `background: var(--${d.tone})` }), el("span", {}, d.label),
      el("span", { class: "muted" }, `${amount(d.value)} (${Math.round((d.value / total) * 100)}%)`)]))),
  ]);
}

function renderFiles(files) {
  const pre = el("pre", { class: "file" });
  const select = el("select", { "aria-label": "파일 고르기" }, files.map((f, i) => el("option", { value: String(i) }, f.name)));
  const show = () => { pre.textContent = files[Number(select.value)].text; };
  select.addEventListener("change", show);
  show();
  return el("div", {}, [select, pre]);
}

// ---- 흐름도: 단계 칸 -> 화살표 -> ... -> 최종 판정. 빠진 수는 칸 바로 밑으로 갈라진다 ---------- //
function arrowIcon() {
  return svg("svg", { viewBox: "0 0 34 14", preserveAspectRatio: "none", "aria-hidden": "true" }, [
    svg("line", { x1: "1", y1: "7", x2: "29", y2: "7", stroke: "currentColor", "stroke-width": "2" }),
    svg("path", { d: "M25 2 L32 7 L25 12", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }),
  ]);
}

function flowNode(step, index) {
  const progress = step.progress && step.progress.expected
    ? [el("span", { class: "caption" }, `${fmt(step.progress.done)} / ${fmt(step.progress.expected)}`), track((step.progress.done / step.progress.expected) * 100, "thin")]
    : null;
  return el(step.link ? "a" : "div", { class: `node ${step.state}`, href: step.link ? runHref(step.link) : null, title: step.title }, [
    el("span", { class: "title" }, [el("span", { class: "no" }, String(index + 1)), koOnly(step.title)]),
    el("span", { class: "value" }, fmt(step.value)),
    step.caption ? el("span", { class: "caption" }, step.caption) : null,
    progress,
    step.notes.length ? el("ul", { class: "notes" }, step.notes.map((n) => el("li", {}, [el("span", {}, n.label), el("b", {}, fmt(n.value))]))) : null,
  ]);
}

function flowBranch(step) {
  if (!step.drops.length) return null;
  return el("div", { class: "branch" }, [el("div", { class: "stem" }), step.drops.map((d) => el(d.link ? "a" : "div",
    { class: `drop ${d.tone || ""}`, href: d.link ? runHref(d.link) : null, title: d.link ? "누르면 이 샘플들이 나와요" : null }, [
      el("div", { class: "drop-head" }, [el("b", {}, `-${fmt(d.value)}`), el("span", {}, koOnly(d.label))]),
      d.detail ? el("div", { class: "drop-detail" }, d.detail) : null,
    ]))]);
}

function renderFlow(data) {
  const parts = [];
  data.steps.forEach((step, i) => {
    parts.push(el("div", { class: "flow-col", style: `--col: ${2 * i + 1}` }, [flowNode(step, i), flowBranch(step)]));
    const next = data.steps[i + 1];
    const live = next ? next.state === "active" : step.state === "active";
    parts.push(el("div", { class: `arrow ${live ? "live" : ""}`, style: `--col: ${2 * i + 2}` },
      [next && next.edge ? el("span", { class: "edge" }, next.edge) : null, arrowIcon()]));
  });
  const total = data.outcomes.reduce((sum, o) => sum + o.value, 0) || 1;
  parts.push(el("div", { class: "outcomes", style: `--col: ${2 * data.steps.length + 1}` }, [
    el("span", { class: "title" }, "최종 판정"),
    data.outcomes.length ? data.outcomes.map((o) => el("a", { class: "outcome", href: runHref(o.link), title: `${o.label} ${o.value}건 (${Math.round((o.value / total) * 100)}%)` }, [
      el("span", { class: "dot", style: `background: var(--${o.tone})` }), el("span", { class: "ol" }, koOnly(o.label)), el("b", {}, fmt(o.value))]))
      : el("span", { class: "muted" }, "아직 없어요"),
  ]));
  return el("div", { class: "flow", style: `--steps: ${data.steps.length}` }, parts);
}

// ---- 검수 깔때기: 검사마다 남은 몫(파랑)과 그 검사에서 빠진 몫(빨강) -------------------------- //
function funnelRow(r, total) {
  const tipBody = () => el("div", {}, [el("b", {}, r.label), r.desc ? el("p", {}, r.desc) : null,
    r.rule ? el("p", { class: "muted" }, `통과 기준: ${r.rule}`) : null,
    el("p", { class: "muted" }, `${r.dropped}건 빠지고 ${r.remaining}건 남음${r.link ? " · 누르면 빠진 샘플 보기" : ""}`)]);
  return withTip(el(r.link ? "a" : "div", { class: `funnel-row ${r.dropped ? "" : "quiet"}`, href: r.link ? runHref(r.link) : null, tabindex: r.link ? null : "0" }, [
    el("span", { class: "name" }, termText(r.label)),
    el("div", { class: "lane" }, [el("span", { class: "kept", style: `width: ${(r.remaining / total) * 100}%` }),
      r.dropped ? el("span", { class: "lost", style: `width: ${(r.dropped / total) * 100}%` }) : null]),
    el("span", { class: "nums" }, [r.dropped ? el("span", { class: "minus" }, `-${fmt(r.dropped)}`) : null, el("b", {}, fmt(r.remaining))]),
  ]), tipBody);
}

function renderFunnel(data) {
  const total = data.total || 1;
  const last = data.rows.length ? data.rows[data.rows.length - 1].remaining : data.total;
  const bar = (width) => el("div", { class: "lane" }, el("span", { class: "kept", style: `width: ${width}%` }));
  return el("div", { class: "funnel" }, [
    el("div", { class: "funnel-row quiet" }, [el("span", { class: "name" }, "검수에 들어온 샘플"), bar(100), el("span", { class: "nums" }, el("b", {}, fmt(data.total)))]),
    data.rows.map((r) => funnelRow(r, total)),
    el("div", { class: "funnel-row result" }, [el("span", { class: "name" }, data.result), bar((last / total) * 100),
      el("span", { class: "nums" }, [el("b", {}, fmt(last)), ` (${Math.round((last / total) * 100)}%)`])]),
  ]);
}

// ---- JSON 나무: ▸를 누르면 펼쳐지고, 키에 마우스를 올리면 설명이 뜬다 ------------------------ //
function nodeInfo(node) {
  const types = state.meta.labels.schema_type || {};
  const shownValue = ["string", "number", "boolean"].includes(node.kind) ? String(node.value) : null;
  return el("div", { class: "ninfo" }, [
    el("div", { class: "npath" }, node.path || "(맨 위)"),
    el("p", { class: "ndesc" }, node.desc || "설명이 아직 없어요"),
    el("div", { class: "nchips" }, [node.type ? el("span", { class: "tag" }, types[node.type] || node.type) : null,
      typeof node.key === "number" ? null : el("span", { class: "tag" }, node.required ? "필수" : "선택"),
      node.children ? el("span", { class: "tag" }, `${node.children.length}개`) : null]),
    node.enum ? el("div", { class: "nenum" }, [el("span", { class: "muted" }, "쓸 수 있는 값 "), node.enum.map((v) => el("code", {}, v))]) : null,
    node.note ? el("p", { class: "nnote" }, node.note) : null,
    shownValue ? el("div", { class: "nvalue" }, [el("span", { class: "muted" }, "예시 값 "), shownValue]) : null,
  ]);
}

function jsonTree(root, { expand = 1, onPick = null } = {}) {
  const groups = [];
  const p = (text) => el("span", { class: "jp" }, text);
  const spacer = () => el("span", { class: "jtog sp" });
  const keyEl = (node) => {
    const isIndex = typeof node.key === "number";
    const key = withTip(el("span", { class: `jkey ${isIndex ? "idx" : ""}`, tabindex: "0" }, isIndex ? String(node.key) : `"${node.key}"`), () => nodeInfo(node));
    if (onPick) key.addEventListener("click", (event) => { event.stopPropagation(); onPick(node, key); });
    return key;
  };
  const valueEl = (node) => {
    if (node.kind === "absent") return el("span", { class: "jv none" }, "없음");
    if (node.kind === "hidden") return el("span", { class: "jv none" }, "모델에는 안 보냄");
    if (node.kind === "string") {
      const long = node.value.length > MAX_STR;
      return el("span", { class: "jv str" }, `"${long ? `${node.value.slice(0, MAX_STR)}…` : node.value}"`);
    }
    if (node.kind === "number" || node.kind === "boolean") return el("span", { class: `jv ${node.kind}` }, String(node.value));
    return el("span", { class: "jv none" }, "null");
  };
  function render(node, depth, last) {
    const comma = last ? null : p(",");
    const key = node.key === "" ? null : [keyEl(node), el("span", { class: "jp colon" }, ":")];
    if (!node.children) return el("div", { class: "jline" }, [spacer(), key, valueEl(node), comma]);
    const [open, close] = node.kind === "array" ? ["[", "]"] : ["{", "}"];
    const count = node.children.length;
    if (!count) return el("div", { class: "jline" }, [spacer(), key, p(`${open}${close}`), comma]);
    const group = el("div", { class: "jgroup" });
    const toggle = el("button", { type: "button", class: "jtog", "aria-label": "펼치기·접기" }, "▸");
    const body = el("div", { class: "jbody" });
    let built = false;
    const set = (on) => {
      if (on && !built) { body.append(...node.children.map((c, i) => render(c, depth + 1, i === count - 1))); built = true; }
      group.classList.toggle("open", on);
      toggle.textContent = on ? "▾" : "▸";
      toggle.setAttribute("aria-expanded", String(on));
    };
    toggle.addEventListener("click", () => set(!group.classList.contains("open")));
    groups.push(set);
    group.append(
      el("div", { class: "jline" }, [toggle, key, p(open), el("span", { class: "jsum" }, node.kind === "array" ? ` ${count}개 ` : ` 키 ${count}개 `), el("span", { class: "jp shut" }, close), comma]),
      body, el("div", { class: "jline jend" }, [spacer(), p(close), comma]));
    set(depth < expand && count <= AUTO_OPEN_MAX);
    return group;
  }
  const tree = el("div", { class: "jtree" }, render(root, 0, true));
  // 펼치면 안쪽 묶음이 새로 생기므로 목록이 늘어나는 동안 계속 돈다
  const expandAll = () => { for (let i = 0; i < groups.length; i += 1) groups[i](true); };
  const collapseAll = () => { groups.forEach((set, i) => set(i === 0)); };
  return { el: tree, expandAll, collapseAll };
}

function rubricStrip(r) {
  return el("div", { class: "strip" }, [
    el("span", {}, [el("span", { class: "muted" }, "점수 "), r.scale.map((s) => pill(scoreTone(s.score, r.scale.length - 1), `${s.score} ${s.label}`))]),
    r.rule ? el("span", {}, [el("span", { class: "muted" }, "통과 기준 "), r.rule]) : null,
    r.judges.length ? el("span", {}, [el("span", { class: "muted" }, "채점 모델 "), r.judges.join(", ")]) : null,
    el("span", {}, [el("span", { class: "muted" }, "채점 프롬프트 "), r.version]),
  ]);
}

function docView(doc) {
  const info = el("aside", { class: "jinfo" }, el("p", { class: "muted" }, "키를 누르면 그 필드의 설명이 여기 고정돼요."));
  let picked = null;
  const pick = (node, key) => { picked?.classList.remove("picked"); picked = key; key.classList.add("picked"); fill(info, nodeInfo(node)); };
  const tree = jsonTree(doc.tree, { expand: doc.expand, onPick: pick });
  return el("div", {}, [
    el("p", { class: "note" }, doc.note),
    doc.rubric ? rubricStrip(doc.rubric) : null,
    el("div", { class: "jtools" }, [el("span", { class: "muted" }, `예시: ${doc.source}`), button("모두 펼치기", tree.expandAll), button("모두 접기", tree.collapseAll)]),
    el("div", { class: "jsplit" }, [tree.el, info]),
  ]);
}

function renderJsonDocs(data) {
  const body = el("div");
  const tabs = data.docs.map((doc) => button(koOnly(doc.title), () => show(doc.key), "seg-btn"));
  function show(key) {
    const index = Math.max(0, data.docs.findIndex((d) => d.key === key));
    state.jsonDoc = data.docs[index].key;
    tabs.forEach((t, i) => t.classList.toggle("active", i === index));
    fill(body, docView(data.docs[index]));
  }
  show(state.jsonDoc);
  return el("div", {}, [el("div", { class: "seg" }, tabs), body]);
}

const RENDERERS = {
  stats: (items) => el("div", { class: "stats" }, items.map((s) => el("div", { class: "stat" }, [el("div", { class: "v" }, fmt(s.value)), el("div", { class: "k" }, s.label)]))),
  keyvals: (items) => el("dl", { class: "keyvals" }, items.flatMap((i) => [el("dt", {}, termText(i.key)), el("dd", {}, fmt(i.value))])),
  table: renderTable,
  donut: renderDonut,
  files: renderFiles,
  flow: renderFlow,
  funnel: renderFunnel,
  json: renderJsonDocs,
  prompts: renderPrompts,
  code: (text) => el("pre", { class: "file" }, text),
  error: (message) => el("pre", { class: "error" }, message),
};

// 근거 표(fold)는 화면에 그리지 않는다(API에만 있다). json·prompts 블록은 제목 대신 고르기 단추가 맨 위에 온다.
function renderBlock(block) {
  const render = RENDERERS[block.kind] || (() => RENDERERS.error(`알 수 없는 블록 종류: ${block.kind}`));
  const titled = !["json", "prompts"].includes(block.kind);
  return el("section", { class: `card ${block.width === "half" ? "half" : ""}` }, [
    titled ? el("h3", {}, termText(block.label || "")) : null,
    block.note && block.kind !== "json" ? el("p", { class: "note" }, block.note) : null,
    render(block.data, block),
  ]);
}

// ---- 맨 위 줄: 실행 고르기, 화면, 이 실행의 요약 -------------------------------------------- //
const currentRun = () => state.meta.runs.find((r) => r.run_id === state.route.run) || null;

function renderTop() {
  const select = $("run-select");
  const dataset = state.meta.dataset;
  fill(select, [dataset ? el("option", { value: dataset.id }, "★ 최종 데이터셋") : null,
    ...state.meta.runs.map((r) => el("option", { value: r.run_id }, `${r.run_id} · ${label("run_status", r.status)}`))]);
  select.value = state.route.run || "";
  if (isDataset()) {
    fill($("tabs"), [[OVERVIEW, "개요"], [TASKS, "태스크"]].map(([name, text]) => el("a", { href: runHref(name), class: `tab ${state.route.view === name ? "active" : ""}` }, text)));
    fill($("facts"), el("span", { class: "muted" }, "검수를 통과한 샘플만 모았어요. 장면을 누르면 원래 실행의 화면으로 가요."));
    return;
  }
  const run = currentRun();
  fill($("tabs"), run ? state.meta.views.map((page) => el("a", { href: runHref(page.name), class: `tab ${state.route.view === page.name ? "active" : ""}` }, [
    page.label, page.name === TASKS && run.n_scenarios ? el("span", { class: "count" }, fmt(run.n_scenarios)) : null])) : null);
  if (!run) { fill($("facts")); return; }
  const running = run.status === "running";
  fill($("facts"), [
    pill(run.status, label("run_status", run.status), (state.meta.labels.run_status_hint || {})[run.status]),
    running ? el("span", {}, `${run.percent ?? "-"}%`) : null,
    el("span", {}, `API ${usd(run.spent_usd)}`),
    duration(run.elapsed_s) ? el("span", {}, duration(run.elapsed_s)) : null,
    running && duration(run.eta_s) ? el("span", {}, `남은 시간 약 ${duration(run.eta_s)}`) : null,
    el("span", { class: "muted", title: `${state.meta.poll_seconds}초마다 새로 읽어요` }, `확인 ${state.checkedAt || "-"}`),
  ]);
}

// ---- 화면: 등록된 분석 블록. 넓은 화면에서 half 블록은 둘씩 나란히 ------------------------------ //
async function showView(view) {
  const page = state.meta.views.find((p) => p.name === view) || state.meta.views[0];
  const { blocks } = await api(`/api/runs/${encodeURIComponent(state.route.run)}/views/${page.name}`);
  fill($("content"), blocks.length ? el("div", { class: "grid" }, blocks.map(renderBlock)) : el("p", { class: "empty" }, "이 실행에는 이 화면에 보여 줄 내용이 없어요."));
}

// ---- 샘플 상세 조각: 설명문, 검수, 입력 (태스크 상세의 모델별 칸이 쓴다) ------------------------- //
const sec = (title, body) => el("div", { class: "dsec" }, [el("div", { class: "dtitle" }, termText(title)), body]);
// 공간 조건: 3.2부터 작업이 시작되는 곳과 끝나는 곳({start, end}), 그 전에는 문장 하나였다
const spaceLines = (space) => {
  if (!space || typeof space === "string") return space || "없음";
  const rows = [["시작", space.start], ["끝", space.end]].filter(([, v]) => v);
  return lines(rows.map(([k, v]) => el("li", {}, [el("span", { class: "tag" }, k), ` ${v}`])));
};
const lines = (items, empty = "없음") => el("ul", { class: "plain" }, items.length ? items : el("li", { class: "muted" }, empty));
const roleName = (key) => term("target_role", key);

// 정리된 모양과 JSON 나무를 오가는 칸. 기본은 읽기 쉬운 쪽이다.
function toggleView(readable, tree) {
  const holder = el("div", {}, readable);
  let asJson = false;
  const flip = button("JSON으로 보기", () => {
    asJson = !asJson;
    flip.textContent = asJson ? "정리해서 보기" : "JSON으로 보기";
    fill(holder, asJson ? jsonTree(tree, { expand: 2 }).el : readable);
  });
  return el("div", {}, [tree ? el("div", { class: "jtools" }, flip) : null, holder]);
}

function objectRoles(targets) {
  return el("div", { class: "roles" }, ROLE_KEYS.map((key) => el("div", { class: `role r-${key}` }, [
    el("div", { class: "rt" }, termText(roleName(key))),
    lines((targets?.[key] || []).map((o) => el("li", {}, [el("b", {}, o.ref), o.location ? ` · ${o.location}` : "", o.state ? ` · ${o.state}` : "", o.note ? ` · ${o.note}` : ""]))),
  ])));
}

function tsdTab(sample) {
  const tsd = sample.output?.task_situation_description;
  if (!tsd) return el("p", { class: "muted" }, "설명문이 없어요.");
  const view = sample.view;
  const legacy = typeof tsd.task_goal !== "object"; // 1.x 설명문은 task_goal이 문장 하나였다
  const goal = legacy ? { summary: tsd.task_goal } : tsd.task_goal;
  const st = tsd.spatiotemporal_conditions || {};
  const goalLine = (g) => el("li", {}, [g.text, g.initial ? [" ", el("span", { class: "tag" }, `처음 씬: ${g.initial}`)] : null]);
  // 목표 상태가 여러 경우면(어느 그릇이든) 경우마다 묶어 보인다. 경우 안은 모두 성립, 경우들 중 하나면 된다
  const goalCases = view.goal_cases.length > 1
    ? el("div", {}, view.goal_cases.map((c, i) => el("div", { class: "case" }, [el("div", { class: "muted small" }, `경우 ${i + 1}`), lines(c.map(goalLine))])))
    : null;
  const constraints = (tsd.constraints || []).map((c) => el("li", {}, [el("span", { class: "tag" }, koOnly(term("constraint", c.type))), ` ${c.description}`]));
  const readable = el("div", {}, [
    el("p", { class: "goal-line" }, goal.summary),
    el("dl", { class: "tsd" }, [
      el("dt", {}, termText(en("목표 상태", "goal_state"))), el("dd", {}, goalCases || lines((view.goal_cases[0] || []).map(goalLine), legacy ? "예전 버전이라 없음" : "없음")),
      // 완료 판정은 3.2까지의 설명문에만 있던 칸이다. 기록에 있을 때만 보인다
      ...(view.condition_lines.length ? [el("dt", {}, termText(en("완료 판정", "completion_conditions"))), el("dd", {}, lines(view.condition_lines.map((c) => el("li", {}, c))))] : []),
      el("dt", {}, termText(en("시간", "time"))), el("dd", {}, st.time || "없음"),
      el("dt", {}, termText(en("공간", "space"))), el("dd", {}, spaceLines(st.space)),
      el("dt", {}, termText(en("대상 물체", "target_objects"))), el("dd", {}, objectRoles(tsd.target_objects)),
      el("dt", {}, termText(en("제약", "constraints"))), el("dd", {}, lines(constraints)),
    ]),
  ]);
  return toggleView(readable, view.tsd_json);
}

function checkChain(view) {
  const result = (passed) => (passed === true ? ["pass", "통과"] : passed === false ? ["fail", "미달"] : ["skip", "건너뜀"]);
  return el("div", { class: "chain" }, view.checks.flatMap((c, i) => {
    const [cls, text] = result(c.passed);
    return [i ? el("span", { class: "sep", "aria-hidden": "true" }, "→") : null, pill(cls, `${koOnly(term("check", c.check))} ${text}`)];
  }));
}

function checklistCards(view) {
  return el("div", { class: "items" }, view.checklist.map((it) => el("div", { class: "item" }, [
    el("div", { class: "item-head" }, [el("span", { class: "item-name" }, termText(it.label)), pill(scoreTone(it.score, view.score_max), `${it.score}점 ${it.score_label}`)]),
    it.criteria ? el("p", { class: "item-crit" }, `기준: ${it.criteria}`) : null,
    el("p", { class: "item-why" }, it.rationale),
  ])));
}

function verificationTab(sample) {
  const v = sample.meta?.verification;
  if (!v) return el("p", { class: "muted" }, "검수 결과가 없어요.");
  const view = sample.view;
  const probe = v.sufficiency_probe;
  const oracleLine = (o) => el("li", {}, [pill(o.matched ? "pass" : o.already_holds ? "skip" : "fail", o.matched ? "맞힘" : o.already_holds ? "이미 성립" : "빠짐"), ` ${o.text}`]);
  const alternatives = [...new Set(view.oracle_lines.map((o) => o.alternative))];
  const oracle = alternatives.length > 1
    ? alternatives.map((k) => el("li", { class: "case" }, [el("div", { class: "muted small" }, `정답 경우 ${k + 1}`), lines(view.oracle_lines.filter((o) => o.alternative === k).map(oracleLine))]))
    : view.oracle_lines.map(oracleLine);
  return el("div", {}, [
    sec("검사 순서", checkChain(view)),
    view.checklist.length ? sec("체크리스트", checklistCards(view)) : null,
    probe ? sec(en("정보 충분성", "probe"), el("p", {}, [pill(scoreTone(probe.score, view.score_max), `${probe.score}/${view.score_max}`), " ",
      probe.missing_info?.length ? `빠진 정보: ${probe.missing_info.join(" / ")}` : "빠진 정보 없음"])) : null,
    (v.goal?.problems || []).length ? sec(en("목표 상태 검사", "goal"), lines(v.goal.problems.map((x) => el("li", {}, x)))) : null,
    oracle.length ? sec(en("정답 대조", "goal_oracle"), el("div", {}, [view.oracle_note ? el("p", { class: "muted small" }, view.oracle_note) : null, lines(oracle)])) : null,
  ]);
}

const OUTPUT_RENDER = {
  tsd: tsdTab,
  verification: verificationTab,
  reasoning: (s) => el("p", { class: "reasoning" }, s.intermediate?.reasoning || "추론이 없어요."),
};

// ---- 태스크 화면 1: 장면 갤러리. 카드 하나 = 시뮬레이션 장면 하나 (시작 화면, 지시, 모델별 판정) ----------- //
const frameUrl = (sampleId) => `/api/runs/${encodeURIComponent(state.route.run)}/samples/${encodeURIComponent(sampleId)}/frame`;

function frameImage(sampleId, cls) {
  const img = el("img", { class: cls, alt: "시작 장면", loading: "lazy", src: frameUrl(sampleId) });
  img.addEventListener("error", () => img.replaceWith(el("div", { class: `${cls} noframe` }, "장면 이미지 없음")));
  return img;
}

function verdictChips(samples) {
  return el("div", { class: "vchips" }, samples.map((s) => pill(s.status, `${s.model} ${label("status", s.status)}`, s.failed_at ? `걸린 곳: ${s.failed_at}` : null)));
}

function taskCard(t) {
  return el("a", { class: "tcard", href: runHref(`${TASKS}/${encodeURIComponent(t.scenario_id)}`) }, [
    t.samples.length ? frameImage(t.samples[0].sample_id, "thumb") : el("div", { class: "thumb noframe" }, "샘플 없음"),
    el("div", { class: "tc-body" }, [
      el("div", { class: "tc-head" }, [el("b", { class: "mono" }, t.task), t.family ? el("span", { class: "tag" }, label("family", t.family)) : null]),
      el("p", { class: "tc-inst" }, t.instruction || t.instruction_en),
      verdictChips(t.samples),
    ]),
  ]);
}

// 결과 필터: 장면 안 샘플들의 판정으로 거른다. 개요에서 넘어온 판정(accepted, rejected 등)도 같은 칸으로 받는다.
const OUTCOME_FILTERS = {
  accepted: ["채택이 나온 장면", (t) => t.accepted],
  rejected: ["미달이 있는 장면", (t) => t.samples.some((s) => s.status === "rejected")],
  all_rejected: ["전부 미달인 장면", (t) => t.all_rejected],
  split: ["모델끼리 갈린 장면", (t) => t.split],
  alternate: ["차순위가 있는 장면", (t) => t.samples.some((s) => s.status === "alternate")],
  needs_review: ["재검토가 있는 장면", (t) => t.samples.some((s) => s.status === "needs_review")],
};

async function showTaskGallery(outcome, check) {
  const run = state.route.run;
  state.tasks[run] ||= (await api(`/api/runs/${encodeURIComponent(run)}/tasks`)).tasks;
  const all = state.tasks[run];
  if (!all.length) { fill($("content"), el("p", { class: "empty" }, "이 실행에는 아직 장면이 없어요.")); return; }
  const option = (value, text) => el("option", { value }, text);
  const checkNames = Object.fromEntries(all.flatMap((t) => t.samples.filter((s) => s.failed_check).map((s) => [s.failed_check, koOnly(s.failed_at)])));
  const f = {
    outcome: el("select", { "aria-label": "결과" }, [option("", "모든 장면"), ...Object.entries(OUTCOME_FILTERS).map(([k, [text]]) => option(k, text))]),
    check: el("select", { "aria-label": "걸린 단계" }, [option("", "모든 걸린 단계"), ...Object.entries(checkNames).map(([k, v]) => option(k, `${v}에서 걸림`))]),
    family: el("select", { "aria-label": "분류" }, [option("", "모든 분류"), ...[...new Set(all.map((t) => t.family).filter(Boolean))].map((v) => option(v, label("family", v)))]),
    search: el("input", { type: "search", placeholder: "태스크나 지시 찾기", "aria-label": "찾기" }),
  };
  f.outcome.value = OUTCOME_FILTERS[outcome] ? outcome : "";
  f.check.value = checkNames[check] ? check : "";
  const grid = el("div", { class: "tgrid" });
  const count = el("span", { class: "muted small" });
  const draw = () => {
    const text = f.search.value.trim().toLowerCase();
    const rows = all.filter((t) => (!f.outcome.value || OUTCOME_FILTERS[f.outcome.value][1](t)) && (!f.check.value || t.checks.includes(f.check.value))
      && (!f.family.value || t.family === f.family.value) && (!text || `${t.task} ${t.instruction} ${t.instruction_en}`.toLowerCase().includes(text)));
    state.taskOrder = rows.map((t) => t.scenario_id);
    const path = [TASKS, ...(f.outcome.value || f.check.value ? [FILTER, f.outcome.value || "all", f.check.value] : [])].filter(Boolean).join("/");
    history.replaceState(null, "", runHref(path));
    state.galleryHash = location.hash;
    count.textContent = `장면 ${rows.length}개`;
    fill(grid, rows.length ? rows.map(taskCard) : el("p", { class: "empty" }, "조건에 맞는 장면이 없어요."));
  };
  for (const input of [f.outcome, f.check, f.family]) input.addEventListener("change", draw);
  f.search.addEventListener("input", draw);
  fill($("content"), el("section", { class: "card" }, [
    el("div", { class: "filters" }, [f.outcome, f.check, f.family, f.search, count]), grid,
  ]));
  draw();
}

// ---- 최종 데이터셋: 여러 실행의 검수 통과 샘플. 장면 상세는 원래 실행의 화면을 연다 ---------------------- //
const isDataset = () => Boolean(state.meta.dataset) && state.route.run === state.meta.dataset.id;
const DATASET_PAGE = 120; // 한 번에 그리는 카드 수 (수만 개라 나눠 그린다)
const datasetType = (t) => (t.composed ? t.task : "원 과제");

async function showDatasetOverview() {
  const { blocks } = await api("/api/dataset/overview");
  fill($("content"), el("div", { class: "grid" }, blocks.map(renderBlock)));
}

function datasetCard(t) {
  const img = el("img", { class: "thumb", alt: "시작 장면", loading: "lazy", src: `/api/runs/${encodeURIComponent(t.run)}/samples/${encodeURIComponent(t.sample_id)}/frame` });
  img.addEventListener("error", () => img.replaceWith(el("div", { class: "thumb noframe" }, "장면 이미지 없음")));
  return el("a", { class: "tcard", href: `#/${encodeURIComponent(t.run)}/${TASKS}/${encodeURIComponent(t.scenario_id)}` }, [
    img,
    el("div", { class: "tc-body" }, [
      el("div", { class: "tc-head" }, [el("b", { class: "mono" }, t.composed ? t.task : `원 과제 · ${t.task}`), el("span", { class: "tag" }, label("domain", t.domain))]),
      el("p", { class: "tc-inst" }, t.instruction),
      el("div", { class: "vchips" }, [el("span", { class: "tag" }, t.model), el("span", { class: "muted small" }, t.run)]),
    ]),
  ]);
}

async function showDatasetTasks() {
  if (!state.datasetRows) fill($("content"), el("p", { class: "empty" }, "최종 데이터셋을 모으는 중이에요. 처음 한 번은 30초쯤 걸려요."));
  state.datasetRows ||= (await api("/api/dataset/tasks")).tasks;
  state.taskOrder = [];
  const all = state.datasetRows;
  const option = (value, text) => el("option", { value }, text);
  const f = {
    domain: el("select", { "aria-label": "도메인" }, [option("", "모든 도메인"), ...state.meta.dataset.domains.map((d) => option(d, label("domain", d)))]),
    type: el("select", { "aria-label": "유형" }),
    model: el("select", { "aria-label": "에이전트" }, [option("", "모든 에이전트"), ...[...new Set(all.map((t) => t.model))].sort().map((m) => option(m, m))]),
    search: el("input", { type: "search", placeholder: "유형이나 지시 찾기", "aria-label": "찾기" }),
  };
  const typeOptions = () => {
    const keep = f.type.value;
    const names = [...new Set(all.filter((t) => !f.domain.value || t.domain === f.domain.value).map(datasetType))].sort();
    fill(f.type, [option("", "모든 유형"), ...names.map((n) => option(n, n))]);
    f.type.value = names.includes(keep) ? keep : "";
  };
  const grid = el("div", { class: "tgrid" });
  const count = el("span", { class: "muted small" });
  let rows = [];
  let shown = DATASET_PAGE;
  const more = button("더 보기", () => { shown += DATASET_PAGE; paint(); });
  const paint = () => {
    fill(grid, rows.length ? rows.slice(0, shown).map(datasetCard) : el("p", { class: "empty" }, "조건에 맞는 샘플이 없어요."));
    more.hidden = rows.length <= shown;
  };
  const draw = () => {
    const text = f.search.value.trim().toLowerCase();
    rows = all.filter((t) => (!f.domain.value || t.domain === f.domain.value) && (!f.type.value || datasetType(t) === f.type.value)
      && (!f.model.value || t.model === f.model.value) && (!text || `${t.task} ${t.instruction}`.toLowerCase().includes(text)));
    shown = DATASET_PAGE;
    count.textContent = `샘플 ${fmt(rows.length)}개`;
    paint();
  };
  f.domain.addEventListener("change", () => { typeOptions(); draw(); });
  for (const input of [f.type, f.model]) input.addEventListener("change", draw);
  f.search.addEventListener("input", draw);
  typeOptions();
  fill($("content"), el("section", { class: "card" }, [el("div", { class: "filters" }, [f.domain, f.type, f.model, f.search, count]), grid, el("p", {}, more)]));
  draw();
}

// ---- 태스크 화면 2: 장면 하나. 시작 화면 + 모델이 받은 세계 상태 -> 지시 -> 모델별 출력 -> 채워진 프롬프트 ---- //
function instructionCard(task) {
  const rw = task.rewrite || {};
  const resolv = rw.resolvability;
  return el("section", { class: "card" }, [
    el("h3", {}, "지시"),
    el("div", { class: "inst" }, [
      el("div", { class: "inst-col" }, [el("div", { class: "dtitle" }, "원문 지시 (영어)"), el("p", {}, task.instruction_en || "-")]),
      el("div", { class: "inst-arrow", "aria-hidden": "true" }, arrowIcon()),
      el("div", { class: "inst-col" }, [el("div", { class: "dtitle" }, termText(en("다시 쓴 지시", "user_instruction"))), el("p", { class: "quote" }, rw.instruction || "-"),
        el("div", { class: "vchips" }, [rw.specificity ? el("span", { class: "tag" }, `구체성: ${rw.specificity}`) : null, rw.fallback_used ? el("span", { class: "tag" }, "한 번 더 구체적으로 다시 씀") : null])]),
    ]),
    resolv ? el("p", { class: "resolv" }, [pill(resolv.pass ? "pass" : "fail", `${koOnly(term("check", "resolvability"))} ${resolv.pass ? "됨" : "안 됨"}`), " ", resolv.rationale || ""]) : null,
  ]);
}

function outputColumn(brief) {
  const sample = brief.detail;
  const body = el("div");
  const tabs = OUTPUT_TABS.map(([key, name]) => button(name, () => show(key), "seg-btn"));
  function show(key) {
    state.outputTab = key;
    tabs.forEach((t, i) => t.classList.toggle("active", OUTPUT_TABS[i][0] === key));
    fill(body, OUTPUT_RENDER[key](sample));
  }
  const reason = sample.view.reason;
  const column = el("div", { class: "ocol" }, [
    el("div", { class: "ohead" }, [el("b", {}, brief.model), pill(brief.status, label("status", brief.status)),
      brief.checklist === null || brief.checklist === undefined ? null : el("span", { class: "muted small" }, `체크리스트 ${brief.checklist}/${brief.checklist_max}`)]),
    reason ? el("div", { class: "banner bad" }, [el("b", {}, `걸린 곳: ${reason.check}`), el("p", {}, reason.text)]) : el("div", { class: "banner good" }, el("b", {}, "모든 검사를 통과했어요")),
    el("div", { class: "seg" }, tabs), body,
  ]);
  show(state.outputTab);
  return column;
}

// 프롬프트 원문의 <<자리>>를 칩으로 바꿔 보여 준다 (무엇이 들어가는 자리인지 마우스를 올리면 나온다)
function withSlots(text) {
  const slots = state.meta.labels.slot || {};
  return text.split(/(<<[A-Z_]+>>)/).map((part) => {
    const name = /^<<([A-Z_]+)>>$/.exec(part)?.[1];
    return name ? el("span", { class: "slot", title: slots[name] || name }, slots[name] ? `${name} · ${slots[name]}` : name) : part;
  });
}

function promptCard(prompt) {
  if (!prompt) return null;
  return el("section", { class: "card" }, [
    el("h3", {}, `이 장면으로 채운 생성 프롬프트 (${prompt.version})`),
    el("p", { class: "note" }, "유저 메시지는 모델이 받은 그대로이고, 시스템 메시지는 이 실행이 쓴 템플릿이에요."),
    el("details", {}, [el("summary", {}, "시스템 메시지 (역할, 지침, 출력 형식, 예시)"), el("pre", { class: "file" }, withSlots(prompt.system))]),
    el("details", { open: "" }, [el("summary", {}, "유저 메시지 (지시 + 환경 컨텍스트)"), el("pre", { class: "file" }, prompt.user)]),
  ]);
}

function neighborLinks(id) {
  const at = state.taskOrder.indexOf(id);
  const link = (offset, text) => {
    const target = at >= 0 ? state.taskOrder[at + offset] : null;
    return target ? el("a", { class: "btn", href: runHref(`${TASKS}/${encodeURIComponent(target)}`) }, text) : el("span", { class: "btn off" }, text);
  };
  return [link(-1, "← 이전 장면"), at >= 0 ? el("span", { class: "muted small" }, `${at + 1} / ${state.taskOrder.length}`) : null, link(1, "다음 장면 →")];
}

async function showTaskDetail(id) {
  const run = state.route.run;
  const key = `${run}/${id}`;
  fill($("content"), el("p", { class: "muted" }, "불러오는 중이에요."));
  state.taskDetails[key] ||= (await api(`/api/runs/${encodeURIComponent(run)}/tasks/${encodeURIComponent(id)}`)).task;
  // 이전·다음은 갤러리에서 거른 순서를 따른다. 그 목록 밖의 장면을 바로 열었으면 전체 장면 순서를 쓴다.
  if (!state.taskOrder.includes(id)) {
    state.tasks[run] ||= (await api(`/api/runs/${encodeURIComponent(run)}/tasks`)).tasks;
    state.taskOrder = state.tasks[run].map((t) => t.scenario_id);
  }
  const task = state.taskDetails[key];
  fill($("content"), el("div", { class: "grid" }, [
    el("div", { class: "tbar" }, [el("a", { class: "btn", href: state.galleryHash || runHref(TASKS) }, "장면 목록"), el("b", { class: "mono ttitle" }, task.task),
      task.family ? el("span", { class: "tag" }, label("family", task.family)) : null, task.activity ? el("span", { class: "muted small" }, task.activity) : null,
      el("span", { class: "tbar-nav" }, neighborLinks(id))]),
    el("section", { class: "card half" }, [el("h3", {}, "시연 첫 프레임"),
      task.samples.length ? frameImage(task.samples[0].sample_id, "scene-img") : null]),
    el("section", { class: "card half" }, [el("h3", {}, termText(en("모델이 받은 세계 상태", "world_state"))),
      el("pre", { class: "file world" }, task.world_state || "-")]),
    instructionCard(task),
    el("section", { class: "card" }, [el("h3", {}, "모델별 출력"),
      el("div", { class: "ocols" }, task.samples.map((brief) => outputColumn(brief)))]),
    promptCard(task.prompt),
  ]));
}

async function showTasks(rest) {
  if (rest[0] && rest[0] !== FILTER) await showTaskDetail(rest[0]);
  else await showTaskGallery(rest[1], rest[2]);
}

// ---- 프롬프트 화면: 역할을 고르면 그 프롬프트의 구조, 채워지는 자리, 돌려받는 출력, 버전 차이 ------------- //
function promptSection(s) {
  return el("div", { class: "psec" }, [
    s.title ? el("div", { class: "ptitle" }, s.title) : null,
    s.text ? el("p", { class: "ptext" }, withSlots(s.text)) : null,
    s.items.length ? el("ol", { class: "pitems" }, s.items.map((item) => el("li", {}, [withSlots(item.text),
      item.sub.length ? el("ul", { class: "psub" }, item.sub.map((x) => el("li", {}, withSlots(x.replace(/^[-·]\s*/, ""))))) : null]))) : null,
  ]);
}

function diffView(r) {
  if (!r.diffs.length) return el("p", { class: "muted small" }, "다른 버전 파일이 없어요.");
  const holder = el("div");
  const select = el("select", { "aria-label": "비교할 버전" }, r.diffs.map((d, i) => el("option", { value: String(i) }, `${r.version} → ${d.version}`)));
  const show = () => {
    const d = r.diffs[Number(select.value)];
    fill(holder, d.lines.length ? el("pre", { class: "diff" }, d.lines.map((l) => el("div", { class: `dl ${l.op}` }, l.op === "gap" ? "⋯" : `${{ add: "+ ", del: "- ", same: "  " }[l.op]}${l.text}`)))
      : el("p", { class: "muted small" }, "두 버전이 같아요."));
  };
  select.addEventListener("change", show);
  show();
  return el("div", {}, [select, holder]);
}

function promptRole(r, example) {
  return el("div", { class: "prole" }, [
    el("div", { class: "phead" }, [el("p", {}, r.purpose), el("div", { class: "vchips" }, [el("span", { class: "tag" }, `이 실행: ${r.version}`), ...r.models.map((m) => el("span", { class: "tag" }, m))])]),
    el("div", { class: "pcols" }, [
      el("div", {}, [
        el("div", { class: "pblock" }, [el("div", { class: "pblock-title" }, "시스템 메시지: 매번 같은 지시"), r.system.map(promptSection)]),
        el("div", { class: "pblock user" }, [el("div", { class: "pblock-title" }, "유저 메시지: 샘플마다 채워지는 자리"), r.user.map(promptSection)]),
      ]),
      el("div", {}, [
        el("div", { class: "pblock" }, [el("div", { class: "pblock-title" }, "돌려받는 출력"),
          el("p", { class: "muted small" }, example ? `값은 이 실행의 채택 샘플 ${example}에서 왔어요. 키에 마우스를 올리면 설명이 나와요.` : ""), jsonTree(r.output, { expand: 2 }).el]),
        el("div", { class: "pblock" }, [el("div", { class: "pblock-title" }, "다른 버전과 차이"), diffView(r)]),
        el("details", {}, [el("summary", {}, "원문 보기"), el("pre", { class: "file" }, r.raw)]),
      ]),
    ]),
  ]);
}

function renderPrompts(data) {
  const body = el("div");
  const steps = data.roles.map((r) => button([el("b", {}, koOnly(r.label)), el("span", { class: "mono small" }, r.version)], () => show(r.key), "pstep"));
  function show(key) {
    const index = Math.max(0, data.roles.findIndex((r) => r.key === key));
    state.promptRole = data.roles[index].key;
    steps.forEach((s, i) => s.classList.toggle("active", i === index));
    fill(body, promptRole(data.roles[index], data.example));
  }
  const pipe = el("div", { class: "ppipe" }, steps.flatMap((s, i) => (i ? [el("span", { class: "parrow", "aria-hidden": "true" }, arrowIcon()), s] : [s])));
  show(state.promptRole);
  return el("div", {}, [pipe, body]);
}

// ---- 라우팅과 갱신 -------------------------------------------------------- //
function parseRoute() {
  const [, run = "", view = "", ...rest] = (location.hash || "#").split("/").map(decodeURIComponent);
  return { run, view: view || OVERVIEW, rest };
}

async function route() {
  state.route = parseRoute();
  hideTip();
  if (!state.route.run) {
    const fallback = state.meta.default_run;
    if (fallback) location.hash = `#/${encodeURIComponent(fallback)}/${OVERVIEW}`;
    else fill($("content"), el("p", { class: "empty" }, "아직 볼 수 있는 실행이 없어요."));
    return;
  }
  renderTop();
  try {
    if (isDataset()) {
      if (state.route.view === TASKS) await showDatasetTasks();
      else await showDatasetOverview();
      window.scrollTo(0, 0);
      return;
    }
    if (state.route.view === TASKS) await showTasks(state.route.rest);
    else await showView(state.route.view);
    window.scrollTo(0, 0);
  } catch (error) {
    fill($("content"), RENDERERS.error(String(error.message || error)));
  }
}

// 진행 중인 실행만 개요를 다시 그린다. 다른 화면은 보고 있는 상태(펼친 나무, 고른 장면)를 지키려고 그대로 둔다.
async function refresh() {
  if (state.refreshing) return; // 앞 요청이 끝나기 전에 또 묻지 않는다 (요청이 쌓이면 서버가 느려진다)
  state.refreshing = true;
  try {
    state.meta = await api("/api/meta");
    state.checkedAt = hms(new Date());
    renderTop();
    const run = currentRun();
    if (!run || run.status !== "running") return;
    delete state.tasks[run.run_id];
    if (state.route.view === OVERVIEW) await showView(OVERVIEW);
  } finally {
    state.refreshing = false;
  }
}

// 화면 밝기: 기본은 밝은 화면이고, 고른 것은 이 브라우저에만 기억한다 (저장이 막혀 있어도 화면은 그대로 동작한다)
const THEME_KEY = "tsd-dashboard-theme";
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "dark" ? "밝은 화면" : "어두운 화면";
}
function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; } catch { return "light"; }
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch { /* 저장이 막혀도 이번 화면에는 적용된다 */ }
  applyTheme(next);
}

async function start() {
  applyTheme(savedTheme());
  $("theme").addEventListener("click", toggleTheme);
  state.meta = await api("/api/meta");
  state.checkedAt = hms(new Date());
  window.addEventListener("hashchange", route);
  window.addEventListener("scroll", hideTip, { passive: true, capture: true });
  $("run-select").addEventListener("change", (event) => {
    state.taskOrder = [];
    state.galleryHash = null;
    location.hash = `#/${encodeURIComponent(event.target.value)}/${state.route.view || OVERVIEW}`;
  });
  await route();
  setInterval(() => refresh().catch(() => {}), state.meta.poll_seconds * 1000);
}

start().catch((error) => fill($("content"), RENDERERS.error(String(error.message || error))));
