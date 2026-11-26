// 2026-12.robot-tsd.dataset 뷰어: samples.json(샘플 색인)을 읽고, 샘플 상세와 썸네일은 저장소 파일을 직접 읽는다.
// 화면 상태는 URL hash가 전부다: #/overview, #/samples, #/sample/<경로>
const REPO = "KWONDU/2026-12.robot-tsd.dataset";
const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname); // 로컬 확인용: 저장소 폴더를 그대로 띄웠을 때
const RAW = LOCAL ? "../" : `https://raw.githubusercontent.com/${REPO}/main/`;
const TREE = `https://github.com/${REPO}/tree/main/`;
const DOMAINS = { household: "가사", logistics: "물류", manufacturing: "제조" };
const AGENTS = ["gpt-6-sol", "gemini-3.8-flash", "qwen3.8-27b-high"];
const PAGE = 60;
const state = { rows: [], filters: { domain: "", type: "", agent: "", text: "" }, shown: PAGE };

const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  node.append(...[].concat(children).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}
const fmt = (n) => n.toLocaleString("en-US");
const fill = (node, ...children) => node.replaceChildren(...children.flat().filter(Boolean));
const row = (r) => ({ id: r[0], domain: r[1], type: r[2], agent: r[3], path: r[4], instruction: r[5] });

function countTable(rows, label, keyOf) {
  const counts = new Map();
  for (const r of rows) {
    const key = keyOf(r);
    if (!counts.has(key)) counts.set(key, Object.fromEntries([...AGENTS, "sum"].map((a) => [a, 0])));
    counts.get(key)[r.agent] += 1;
    counts.get(key).sum += 1;
  }
  const ordered = [...counts.entries()].sort((a, b) => b[1].sum - a[1].sum);
  const total = Object.fromEntries([...AGENTS, "sum"].map((a) => [a, ordered.reduce((s, [, c]) => s + c[a], 0)]));
  return el("table", {}, [
    el("thead", {}, el("tr", {}, [el("th", {}, label), ...AGENTS.map((a) => el("th", { class: "n" }, a)), el("th", { class: "n" }, "합")])),
    el("tbody", {}, [...ordered, ["합", total]].map(([name, c]) => el("tr", {}, [
      el("td", { class: name === "합" ? "" : "mono" }, name), ...AGENTS.map((a) => el("td", { class: "n" }, fmt(c[a]))), el("td", { class: "n" }, fmt(c.sum))]))),
  ]);
}

function showOverview() {
  const rows = state.rows;
  const byDomain = Object.keys(DOMAINS).map((d) => [d, rows.filter((r) => r.domain === d)]);
  fill($("content"),
    el("section", { class: "card" }, [
      el("h2", {}, "검수를 통과한 샘플"),
      el("p", { class: "muted small" }, "한국어 자연어 지시와 로봇이 인식한 세계 상태를 입력으로, 추론과 작업 상황 설명문(TSD)을 출력으로 해요. 생성 에이전트 셋이 만들고 검수를 통과한 것만 담았어요."),
      el("div", { class: "stats" }, [el("div", { class: "stat" }, [el("b", {}, fmt(rows.length)), "전체"]),
        ...byDomain.map(([d, rs]) => el("div", { class: "stat" }, [el("b", {}, fmt(rs.length)), `${DOMAINS[d]} · 유형 ${new Set(rs.map((r) => r.type)).size}개`]))]),
    ]),
    el("section", { class: "card" }, [el("h2", {}, "도메인 × 에이전트"), countTable(rows, "도메인", (r) => DOMAINS[r.domain])]),
    ...byDomain.map(([d, rs]) => el("section", { class: "card" }, [el("h2", {}, `${DOMAINS[d]}: 작업 유형 × 에이전트`), countTable(rs, "유형", (r) => r.type)])));
}

function card(r) {
  const img = el("img", { src: `${RAW}${r.path}/thumbnail.jpg`, alt: "장면 썸네일", loading: "lazy" });
  return el("a", { class: "tcard", href: `#/sample/${encodeURIComponent(r.path)}` }, [img, el("div", { class: "body" }, [
    el("span", { class: "tag" }, DOMAINS[r.domain]), el("span", { class: "tag mono" }, r.type), el("span", { class: "tag" }, r.agent),
    el("p", { class: "inst" }, r.instruction)])]);
}

function showSamples() {
  const f = state.filters;
  const option = (v, t) => el("option", { value: v }, t);
  const domain = el("select", { "aria-label": "도메인" }, [option("", "모든 도메인"), ...Object.entries(DOMAINS).map(([k, v]) => option(k, v))]);
  const type = el("select", { "aria-label": "작업 유형" });
  const agent = el("select", { "aria-label": "에이전트" }, [option("", "모든 에이전트"), ...AGENTS.map((a) => option(a, a))]);
  const search = el("input", { type: "search", placeholder: "지시나 유형 찾기", "aria-label": "찾기" });
  const count = el("span", { class: "muted small" });
  const grid = el("div", { class: "grid" });
  const more = el("button", { type: "button" }, "더 보기");
  const types = () => {
    const names = [...new Set(state.rows.filter((r) => !f.domain || r.domain === f.domain).map((r) => r.type))].sort();
    fill(type, option("", "모든 유형"), names.map((n) => option(n, n)));
    if (!names.includes(f.type)) f.type = "";
    type.value = f.type;
  };
  let rows = [];
  const paint = () => {
    fill(grid, rows.length ? rows.slice(0, state.shown).map(card) : el("p", { class: "empty" }, "조건에 맞는 샘플이 없어요."));
    more.hidden = rows.length <= state.shown;
  };
  const draw = () => {
    const text = f.text.trim().toLowerCase();
    rows = state.rows.filter((r) => (!f.domain || r.domain === f.domain) && (!f.type || r.type === f.type) && (!f.agent || r.agent === f.agent)
      && (!text || `${r.type} ${r.instruction}`.toLowerCase().includes(text)));
    count.textContent = `샘플 ${fmt(rows.length)}개`;
    paint();
  };
  domain.value = f.domain; agent.value = f.agent; search.value = f.text;
  domain.addEventListener("change", () => { f.domain = domain.value; state.shown = PAGE; types(); draw(); });
  type.addEventListener("change", () => { f.type = type.value; state.shown = PAGE; draw(); });
  agent.addEventListener("change", () => { f.agent = agent.value; state.shown = PAGE; draw(); });
  search.addEventListener("input", () => { f.text = search.value; state.shown = PAGE; draw(); });
  more.addEventListener("click", () => { state.shown += PAGE; paint(); });
  types();
  fill($("content"), el("section", { class: "card" }, [el("div", { class: "filters" }, [domain, type, agent, search, count]), grid, el("div", { class: "more" }, more)]));
  draw();
}

async function showSample(path) {
  fill($("content"), el("p", { class: "empty" }, "샘플을 불러오는 중이에요."));
  const sample = await (await fetch(`${RAW}${path}/sample.json`)).json();
  const tsd = sample.output.task_situation_description;
  const section = (title, body, open = true) => el("details", open ? { open: "" } : {}, [el("summary", {}, title), body]);
  fill($("content"),
    el("p", {}, el("a", { href: "#/samples" }, "← 샘플 목록")),
    el("section", { class: "card" }, el("div", { class: "detail-head" }, [
      el("img", { src: `${RAW}${path}/thumbnail.jpg`, alt: "장면 썸네일" }),
      el("div", { class: "meta" }, [
        el("p", {}, [el("span", { class: "tag" }, DOMAINS[sample.domain]), el("span", { class: "tag mono" }, sample.task_type), el("span", { class: "tag" }, sample.agent)]),
        el("h2", {}, sample.input.instruction),
        el("p", { class: "muted" }, sample.input.instruction_en || ""),
        el("p", { class: "small muted mono" }, sample.id),
        el("p", { class: "small" }, [el("a", { href: `${TREE}${path}`, target: "_blank", rel: "noopener" }, "GitHub에서 폴더 보기"), " · ",
          el("a", { href: `${RAW}${path}/scene.json`, target: "_blank", rel: "noopener" }, "scene.json")]),
      ]),
    ])),
    el("section", { class: "card" }, section("입력: 세계 상태 글 (모델이 받은 그대로)", el("pre", {}, sample.input.world_state || ""))),
    el("section", { class: "card" }, section("출력: 추론", el("pre", {}, sample.output.reasoning || ""))),
    el("section", { class: "card" }, section("출력: 작업 상황 설명문", el("pre", {}, sample.output.task_situation_description_text || ""))),
    el("section", { class: "card" }, section("출력: 작업 상황 설명문 JSON", el("pre", { class: "mono" }, JSON.stringify(tsd, null, 1)), false)));
}

async function route() {
  const [, view = "overview", ...rest] = (location.hash || "#/overview").split("/");
  for (const a of document.querySelectorAll(".tabs a[data-tab]")) a.classList.toggle("active", a.dataset.tab === (view === "sample" ? "samples" : view));
  try {
    if (view === "sample") await showSample(decodeURIComponent(rest.join("/")));
    else if (view === "samples") showSamples();
    else showOverview();
  } catch (error) {
    fill($("content"), el("p", { class: "empty" }, `불러오지 못했어요: ${error.message || error}`));
  }
  window.scrollTo(0, 0);
}

async function start() {
  state.rows = (await (await fetch("samples.json")).json()).map(row);
  window.addEventListener("hashchange", route);
  await route();
}

start().catch((error) => fill($("content"), el("p", { class: "empty" }, `불러오지 못했어요: ${error.message || error}`)));
