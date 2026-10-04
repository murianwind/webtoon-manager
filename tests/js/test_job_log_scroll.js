// 진행 상황 로그(수동 실행/아카이빙/폴더 일괄 이동/수동 다운로드 공통 renderJobLog) — 스크롤 위치 유지.
// 맨 아래를 보고 있으면 새 줄을 따라 내려가고, 위로 올려 읽는 중이면 그 위치를 유지하고, 다시 맨 아래로 내리면 따라가기를 다시 시작한다.
// 새 줄이 없으면 다시 그리지 않는다(텍스트 선택/스크롤 유지).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const { w, run } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: false }, "/api/settings/webtoon-server": { webtoon_server_url: "" } });
  await sleep(60);
  // jsdom은 레이아웃을 계산하지 않아서, 로그 상자 높이(한 줄 20px, 보이는 높이 100px)와 스크롤을 직접 흉내 낸다
  const fakeLayout = (id) => {
    const el = w.document.getElementById(id); let top = 0;
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 100 });
    Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => el.children.length * 20 });
    Object.defineProperty(el, "scrollTop", { configurable: true, get: () => top, set: (v) => { top = Math.max(0, Math.min(v, Math.max(0, el.scrollHeight - el.clientHeight))); } });
    return el;
  };
  const lines = (n) => Array.from({ length: n }, (_, i) => `2026-10-03T10:00:${String(i).padStart(2, "0")}+09:00 — ${i + 1}번째 줄`);
  const render = (job, n) => run(`renderJobLog(${JSON.stringify(job)}, ${JSON.stringify(lines(n))});`);

  for (const job of ["download", "archive", "bulk_move", "manual"]) {
    const el = fakeLayout(`${job}-log`);
    render(job, 10);
    ok(el.scrollTop === 100, `${job}: 처음엔 맨 아래(100)를 보여 줌: ${el.scrollTop}`);
    render(job, 12);
    ok(el.scrollTop === 140, `${job}: 맨 아래를 보고 있으면 새 줄을 따라 내려감(140): ${el.scrollTop}`);
    el.scrollTop = 20; render(job, 15);
    ok(el.scrollTop === 20, `${job}: 위로 올려 읽는 중이면 새 줄이 와도 위치 유지(20): ${el.scrollTop}`);
    render(job, 18);
    ok(el.scrollTop === 20, `${job}: 계속 유지: ${el.scrollTop}`);
    el.scrollTop = el.scrollHeight - el.clientHeight - 10; render(job, 20);       // 맨 아래 거의 근처(10px 위)까지 내리면
    ok(el.scrollTop === 300, `${job}: 다시 맨 아래 근처로 내리면 따라가기 재개(300): ${el.scrollTop}`);
    const first = el.firstElementChild; el.scrollTop = 40; render(job, 20);
    ok(el.firstElementChild === first && el.scrollTop === 40, `${job}: 새 줄이 없으면 다시 그리지 않음(같은 요소, 위치 그대로)`);
    render(job, 0);
    ok(el.children.length === 0, `${job}: 빈 로그로 비우기는 그대로 동작`);
    render(job, 3);
    ok(el.children.length === 3 && el.scrollTop === 0, `${job}: 비운 뒤 새 실행 로그도 정상(짧아서 스크롤 없음)`);
  }
  // 서로 다른 로그 상자는 따로 기억한다
  const a = w.document.getElementById("discovery-log"), b = w.document.getElementById("report-log");
  fakeLayout("discovery-log"); fakeLayout("report-log");
  render("discovery", 10); render("report", 10); a.scrollTop = 0; render("discovery", 12); render("report", 12);
  ok(a.scrollTop === 0 && b.scrollTop === 140, "로그 상자마다 따로: 위로 올린 상자만 위치 유지, 다른 상자는 따라감");
  done();
})();
