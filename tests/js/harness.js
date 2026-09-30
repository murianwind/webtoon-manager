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

function makeApp(routes) {
  const { VirtualConsole } = require("jsdom");
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (!String(e.message).includes("Not implemented")) scriptErrors.push(e.detail ? `${e.message}: ${e.detail.message || e.detail}` : e.message); });
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://localhost/", pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  const calls = [];
  w.fetch = async (url, opts = {}) => {
    const key = String(url).split("?")[0];
    calls.push({ url: String(url), method: opts.method || "GET", body: opts.body });
    const h = routes[String(url)] || routes[key];
    const data = typeof h === "function" ? h(opts, String(url)) : (h === undefined ? {} : h);
    return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)), text: async () => JSON.stringify(data) };
  };
  w.CSS = { escape: (s) => s };
  w.alert = (m) => { w.__alerts.push(m); };
  w.__alerts = [];
  w.confirm = () => (w.__confirm === undefined ? true : w.__confirm);
  const run = (code) => { const s = w.document.createElement("script"); s.textContent = code; w.document.body.appendChild(s); };
  for (const script of appScripts) run(script.code);
  return { w, run, calls, dom };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
function ok(cond, msg) { if (!cond) { failed++; console.log("  ✗ 실패:", msg); } else console.log("  ✓", msg); }
module.exports = { makeApp, sleep, ok, readAppJs, done: () => {
  if (scriptErrors.length) { failed += scriptErrors.length; scriptErrors.forEach((e) => console.log('  ✗ 스크립트 오류:', e.slice(0, 200))); } console.log(failed ? `\n실패 ${failed}건` : "\n전부 통과"); process.exit(failed ? 1 : 0); } };
