// 빠른 상태 스캔 화면 — ① 수동 실행의 "빠른 상태 스캔" 버튼(상태/로그 칸 포함) ② 전체목록 "새로고침"이 오늘/선택한 요일/기타만 범위로 스캔을 요청하고 끝나면 목록을 다시 불러온다
// ③ 기타 탭의 두 문구("완결 웹툰 N개는 …", "기타 원인 …")는 화면에 띄우지 않는다(실행 이력으로 옮겼다)
const { makeApp, idle, ok, done } = require("./harness");
const { listItem } = require("./factories");
(async () => {
  let jobs = { quick_scan: { status: "idle", log: [], started_at: null } };
  const scanRequests = []; let scanSeq = 0;
  const rows = [listItem("naver", 1, "화요일작품", ["tue"], { status: "excluded" }), listItem("naver", 2, "기타작품", [], { status: "excluded" }), listItem("naver", 3, "완결작품", [], { status: "excluded", is_finished: true })];
  const { w, run, calls, wait } = makeApp({
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/naver-list": [], "/api/kakao-list": { items: [], version: "v", refreshing: false }, "/api/webtoons": () => rows, "/api/kakao-webtoons": () => [],
    "/api/jobs/status": () => jobs,
    "/api/jobs/quick-scan/run": (opts) => { scanRequests.push(opts && opts.body ? JSON.parse(opts.body) : null); jobs = { quick_scan: { status: "running", log: [], started_at: "S" + ++scanSeq } }; return { status: "started" }; }, // 요청하면 새 실행이 시작된다(시작 시각이 바뀐다)
  }, { timeScale: 20, now: "2026-11-03T03:00:00Z" }); // 화요일(한국시간)
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const gets = (url) => calls.filter((c) => c.url.startsWith(url) && c.method === "GET").length;

  // Scenario 1: 수동 실행 화면 — 버튼, 상태 배지, 로그 칸. 누르면 범위 없이 요청하고 상태를 새로 읽는다. 이력 화면의 작업 이름도 한글로
  ok($("btn-run-quick-scan") && $("btn-run-quick-scan").textContent.trim() === "빠른 상태 스캔", "수동 실행 버튼");
  ok(!!$("quick_scan-status-badge") && !!$("quick_scan-log"), "상태 배지/로그 칸");
  ok(w.eval("JOB_NAME_LABEL").quick_scan === "빠른 상태 스캔", "이력의 작업 이름");
  $("btn-run-quick-scan").click(); await idle();
  ok(scanRequests.length === 1 && scanRequests[0] === null, "범위 없이 요청: " + JSON.stringify(scanRequests));
  jobs = { quick_scan: { status: "running", log: ["2026-11-03T01:00:00+00:00 — 빠른 상태 스캔 시작 — 대상 12개", "2026-11-03T01:00:05+00:00 — 빠른 상태 스캔 진행 3/12개"] } };
  run("refreshJobStatus();"); await idle();
  ok($("quick_scan-status-badge").textContent === "running" && $("quick_scan-log").textContent.includes("진행 3/12개"), "진행 중 표시: " + $("quick_scan-log").textContent.slice(-40));

  // Scenario 2: 전체목록 새로고침 — 오늘(화)과 기타, 선택한 요일 탭이 있으면 그것까지 범위로 스캔을 요청한다(중복 없이). 요청은 목록을 다시 받은 뒤에
  scanRequests.length = 0; jobs = { quick_scan: { status: "idle", log: [], started_at: null } };
  run("naverListNaverItems = []; naverListKakaoItems = []; combineNaverListCache(); renderNaverList();");
  $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle();
  ok(scanRequests.length === 1 && JSON.stringify(scanRequests[0].scope.slice().sort()) === JSON.stringify(["etc", "tue"]), "처음(오늘 탭 선택): 기타 + 오늘: " + JSON.stringify(scanRequests));
  run("listWindow('naver-list').tab = 'thu';"); scanRequests.length = 0;
  $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle();
  ok(JSON.stringify(scanRequests[0].scope.slice().sort()) === JSON.stringify(["etc", "thu", "tue"]), "선택한 요일(목)도 포함: " + JSON.stringify(scanRequests));
  run("listWindow('naver-list').tab = 'all';"); scanRequests.length = 0; $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle();
  ok(JSON.stringify(scanRequests[0].scope.slice().sort()) === JSON.stringify(["etc", "tue"]), "전체 탭이면 오늘+기타만");
  run("listWindow('naver-list').tab = 'etc';"); scanRequests.length = 0; $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle();
  ok(JSON.stringify(scanRequests[0].scope.slice().sort()) === JSON.stringify(["etc", "tue"]), "기타 탭이면 중복 없이");

  // Scenario 3: 스캔이 끝나면(시작 시각이 바뀌고 running이 아니게 되면) 목록을 한 번 다시 불러온다 — 알아낸 요일/완결이 바로 보이게. 오류로 끝나면 다시 불러오지 않는다
  run("listWindow('naver-list').tab = null;"); jobs = { quick_scan: { status: "idle", log: [], started_at: null } };
  const before = gets("/api/naver-list");
  $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle();
  const afterClick = gets("/api/naver-list"); ok(afterClick === before + 1, "새로고침 자체는 한 번: " + (afterClick - before));
  await wait(2500); await idle(); ok(gets("/api/naver-list") === afterClick, "스캔이 도는 동안에는(감시가 한 번 이상 확인해도) 다시 불러오지 않는다");
  jobs.quick_scan.status = "success"; jobs.quick_scan.log = ["2026-11-03T01:01:00+00:00 — 빠른 상태 스캔 완료 — 확인 3개"];
  await wait(2500); await idle(); ok(gets("/api/naver-list") === afterClick + 1, "끝나면 목록을 한 번 다시 불러온다: " + (gets("/api/naver-list") - afterClick));
  await wait(6000); await idle(); ok(gets("/api/naver-list") === afterClick + 1, "그 뒤로는 다시 안 불러온다");
  const b2 = gets("/api/naver-list"); $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle(); const b2c = gets("/api/naver-list");
  jobs.quick_scan.status = "error"; await wait(2500); await idle(); ok(gets("/api/naver-list") === b2c, "오류로 끝나면 다시 불러오지 않는다");
  // 새로고침을 연달아 누르면 앞의 감시는 버리고 마지막 것만 — 끝났을 때 목록은 한 번만 다시 불러온다
  const b3 = gets("/api/naver-list"); $("btn-refresh-naver-list").click(); await idle(); await wait(100); $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle(); const b3c = gets("/api/naver-list");
  jobs.quick_scan.status = "success"; await wait(2500); await idle(); ok(gets("/api/naver-list") === b3c + 1, "연달아 눌러도 끝났을 때 한 번만: " + (gets("/api/naver-list") - b3c));

  // Scenario 3b: 감시가 헷갈리지 않는다 — (a) 스캔이 아직 도는 중(running)에는 시작 시각이 바뀌었어도 다시 불러오지 않는다 (b) 이전 실행이 끝난 상태(시작 시각이 그대로)에는 반응하지 않는다
  jobs = { quick_scan: { status: "success", log: [], started_at: "OLD" } };                                                     // 이전에 끝난 실행
  const b4 = gets("/api/naver-list"); scanSeq = 100;
  const routeBackup = w.fetch; let keepOld = true;
  w.fetch = async (url, opts = {}) => { if (keepOld && String(url) === "/api/jobs/quick-scan/run") { scanRequests.push(JSON.parse(opts.body)); return { ok: true, status: 200, json: async () => ({ status: "started" }), text: async () => "{}" }; } return routeBackup(url, opts); };
  $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle(); const b4c = gets("/api/naver-list");     // 요청은 갔지만 새 실행이 시작되지 않은 상태(시작 시각 그대로 OLD)
  await wait(3000); await idle(); ok(gets("/api/naver-list") === b4c, "새 실행이 시작되기 전에 이전 실행의 종료(success, 같은 시작 시각)에는 반응하지 않는다: " + (gets("/api/naver-list") - b4c));
  keepOld = false; w.fetch = routeBackup; jobs = { quick_scan: { status: "idle", log: [], started_at: null } };
  $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle(); const b5c = gets("/api/naver-list");
  await wait(2500); await idle(); ok(jobs.quick_scan.status === "running" && gets("/api/naver-list") === b5c, "스캔이 도는 중(running)에는 시작 시각이 바뀌었어도 다시 불러오지 않는다");
  jobs.quick_scan.status = "success"; await wait(2500); await idle();

  // Scenario 3c: 스캔 요청이 실패해도(서버 오류 등) 새로고침은 정상으로 끝난다(예외를 밖으로 던지지 않는다) — 목록은 그대로 보이고 다시 불러오는 감시도 시작하지 않는다
  w.fetch = async (url, opts = {}) => { if (String(url) === "/api/jobs/quick-scan/run") throw new Error("스캔 요청 서버 오류"); return routeBackup(url, opts); };
  let unhandled = null; const onRejection = (e) => { unhandled = e; }; process.once("unhandledRejection", onRejection);
  const b6 = gets("/api/naver-list"); $("btn-refresh-naver-list").click(); await idle(); await wait(100); await idle(); await new Promise((r) => setTimeout(r, 20));
  ok(unhandled === null && gets("/api/naver-list") === b6 + 1, "요청이 실패해도 새로고침은 정상으로 끝난다(처리되지 않은 오류 없음, 목록은 한 번 받음)");
  process.removeListener("unhandledRejection", onRejection); w.fetch = routeBackup;

  // Scenario 4: 기타 탭 문구 제거 — 제외됨에서 완결을 가리되 "완결 웹툰 N개는 …" 줄은 없고, 기타 탭을 봐도 "기타 원인" 요청/줄이 없다
  run("subscriptionCache.excluded = " + JSON.stringify(rows) + "; renderSubscriptionTab('excluded');");
  const titles = () => [...w.document.querySelectorAll("#excluded-list .webtoon-card-title")].map((e) => e.textContent.trim());
  [...$("excluded-weekday-tabs").querySelectorAll(".weekday-tab")].find((t) => t.firstChild.textContent.trim() === "기타").click(); await idle();
  ok(JSON.stringify(titles()) === JSON.stringify(["기타작품"]), "완결은 가려진다: " + titles());
  ok(!$("excluded-finished-note") && !$("excluded-etc-note") && !$("naver-list-etc-note"), "화면 문구 칸이 없다");
  ok(!calls.some((c) => c.url.startsWith("/api/diagnostics")), "원인 요청도 안 한다");
  done();
})();
