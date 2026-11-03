const fs = require("fs");
const { JSDOM } = require("jsdom");
const path = require("path");
const ROOT = path.join(__dirname, "..", "..", "app", "static") + path.sep;
const rawHtml = fs.readFileSync(ROOT + "index.html", "utf8");
// 브라우저처럼 index.html의 <script src>를 적힌 순서대로 로드한다(파일마다 별도 클래식 스크립트 — 파일 사이 참조/hoisting 오류가 그대로 드러난다)
const scriptSrcs = [...rawHtml.matchAll(/<script src="\/([^"]+)"><\/script>/g)].map((m) => m[1]);
const html = rawHtml.replace(/<script src="[^"]+"><\/script>/g, "");
const appScripts = scriptSrcs.map((src) => ({ src, code: fs.readFileSync(ROOT + src, "utf8") }));
const readAppJs = () => appScripts.map((s) => s.code).join("\n"); // 문자열 검사용(전체 코드)
const scriptErrors = [];

// 진행 중인 fetch 수 — idle()이 "다 끝났을 때"를 알아내는 데 쓴다(프로세스 하나가 앱 하나를 쓰므로 모듈 전역으로 충분하다)
let pendingFetches = 0;

// makeApp(routes, { timeScale }): timeScale=n이면 앱의 setTimeout/setInterval 시간이 n배 빨라진다(3초 폴링을 0.3초로). 타이머로 도는 동작을 고정 sleep 없이 빠르게 확인할 때 쓴다.
function makeApp(routes, { timeScale = 1 } = {}) {
  const { VirtualConsole } = require("jsdom");
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (!String(e.message).includes("Not implemented")) scriptErrors.push(e.detail ? `${e.message}: ${e.detail.message || e.detail}` : e.message); });
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://localhost/", pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  const calls = [];
  w.fetch = async (url, opts = {}) => {
    pendingFetches++;
    try {
      const key = String(url).split("?")[0];
      calls.push({ url: String(url), method: opts.method || "GET", body: opts.body });
      const h = routes[String(url)] || routes[key];
      const data = typeof h === "function" ? await h(opts, String(url)) : (h === undefined ? {} : h);
      return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)), text: async () => JSON.stringify(data) };
    } finally {
      pendingFetches--;
    }
  };
  if (timeScale !== 1) {
    const realSetTimeout = w.setTimeout.bind(w), realSetInterval = w.setInterval.bind(w);
    w.setTimeout = (fn, ms, ...args) => realSetTimeout(fn, (ms || 0) / timeScale, ...args);
    w.setInterval = (fn, ms, ...args) => realSetInterval(fn, (ms || 0) / timeScale, ...args);
  }
  w.CSS = { escape: (s) => s };
  w.alert = (m) => { w.__alerts.push(m); };
  w.__alerts = [];
  w.confirm = () => (w.__confirm === undefined ? true : w.__confirm);
  const run = (code) => { const s = w.document.createElement("script"); s.textContent = code; w.document.body.appendChild(s); };
  for (const script of appScripts) run(script.code);
  // wait(ms): 앱 시간 기준 ms가 지나기를 기다린다(timeScale이 있으면 그만큼 짧게) — 고정 sleep 대신 쓰는, 시간 축소에 맞는 대기
  const wait = (ms) => new Promise((r) => setTimeout(r, ms / timeScale + 5));
  return { w, run, calls, dom, wait };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// idle(): 진행 중인 fetch가 모두 끝나고, 그 결과로 이어지는 비동기 후속 처리(그리기 등)까지 흘러간 뒤에 돌아온다 — "몇 ms 기다리면 되겠지" 대신 "다 끝났을 때" 진행.
// (화면이 타이머로 미루는 동작(검색 지연, 폴링)은 기다리지 않는다 — 그런 것은 timeScale과 wait를 쓴다)
async function idle() {
  const tick = () => new Promise((r) => setImmediate(r));
  for (let quiet = 0; quiet < 3; ) {
    await tick();
    quiet = pendingFetches === 0 ? quiet + 1 : 0;
  }
}

// waitFor(조건, 설명, { timeout }): 조건이 참(또는 값)을 돌려줄 때까지 짧게 반복 확인하고 그 값을 돌려준다. 끝내 안 되면 무엇을 기다렸는지 담아 실패한다.
async function waitFor(condition, what = "조건", { timeout = 3000, interval = 5 } = {}) {
  const start = Date.now();
  for (;;) {
    const value = condition();
    if (value) return value;
    if (Date.now() - start > timeout) throw new Error(`${what} 기다리다 ${timeout}ms가 지났습니다`);
    await sleep(interval);
  }
}
let failed = 0;
function ok(cond, msg) { if (!cond) { failed++; console.log("  ✗ 실패:", msg); } else console.log("  ✓", msg); }
module.exports = { makeApp, sleep, idle, waitFor, ok, readAppJs, done: () => {
  if (scriptErrors.length) { failed += scriptErrors.length; scriptErrors.forEach((e) => console.log('  ✗ 스크립트 오류:', e.slice(0, 200))); } console.log(failed ? `\n실패 ${failed}건` : "\n전부 통과"); process.exit(failed ? 1 : 0); } };
