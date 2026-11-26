// 설정 > 실행 스케줄: "기다무 충전 시각에 맞춰 자동 다운로드" 옵션(켜기/끄기, 즉시 저장)과, 켜면 그 아래에 다운로드 예정 시각과 웹툰 제목 목록이 보인다.
const { makeApp, sleep, idle, ok, done } = require("./harness");
(async () => {
  let enabled = false;
  let items = [
    { title_id: 1, title: "도굴왕: 엔드라인", next_at: "2026-10-13T12:00:00+00:00", due_at: "2026-10-13T12:01:10+00:00" },
    { title_id: 2, title: "나 혼자만 레벨업", next_at: "2026-10-14T03:30:00+00:00", due_at: "2026-10-14T03:31:20+00:00" },
  ];
  const posts = [];
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings/waitfree-sync": (opts) => { if (opts && opts.method === "POST") { enabled = JSON.parse(opts.body).enabled; posts.push(enabled); } return { enabled }; },
    "/api/waitfree/schedule": () => ({ enabled, items: enabled ? items : [] }),
  };
  const { w, run } = makeApp(routes); await idle();
  const $ = (id) => w.document.getElementById(id);
  const hidden = (id) => $(id).classList.contains("hidden");
  const rows = () => [...w.document.querySelectorAll("#waitfree-schedule-list li")].map((li) => li.textContent.replace(/\s+/g, " ").trim());

  // Scenario 1: 처음(옵션 꺼짐) — 체크 해제, 설명과 예정 목록은 숨김. 옵션은 "카카오웹툰 관리" 카드 안(대여권 옵션 아래)에 있다
  run("window.__p = loadWaitfreeSyncSetting();"); await w.__p; await idle();
  ok($("waitfree-sync-toggle").checked === false && hidden("waitfree-schedule-box"), "꺼짐: 체크 해제, 예정 목록 숨김");
  const card = $("settings-card-kakao"), rental = $("kakao-rental-auto-group");
  ok(card.contains($("waitfree-sync-toggle")) && !$("settings-card-run").contains($("waitfree-sync-toggle")) && !!(rental.compareDocumentPosition($("waitfree-sync-toggle")) & w.Node.DOCUMENT_POSITION_FOLLOWING), "카카오웹툰 관리 카드 안, 대여권 옵션 아래에 있다");
  ok(/정기 다운로드는 충전 시각 전인 기다무 작품을 건너뛰/.test($("waitfree-sync-group").textContent) && /수동 실행은/.test($("waitfree-sync-group").textContent), "옵션 설명: 정기 다운로드가 건너뜀, 수동 실행은 그대로");

  // Scenario 2: 켜면 바로 저장(요청 한 번)하고 그 아래에 "다운로드 예정" 목록이 시각 순(서버가 준 순서)으로 나온다 — 시각(한국시간)과 웹툰 제목, 남은 시간
  $("waitfree-sync-toggle").checked = true; $("waitfree-sync-toggle").dispatchEvent(new w.Event("change")); await idle();
  ok(posts.length === 1 && posts[0] === true && $("waitfree-sync-save-result").textContent === "저장했습니다.", "켜면 즉시 저장: " + posts.length + " / " + $("waitfree-sync-save-result").textContent);
  ok(!hidden("waitfree-schedule-box"), "켜면 예정 목록이 보인다");
  const r = rows();
  ok(r.length === 2 && /10월 13일 21:01/.test(r[0]) && /도굴왕: 엔드라인/.test(r[0]) && /10월 14일 12:31/.test(r[1]) && /나 혼자만 레벨업/.test(r[1]), "한국시간 시각 + 제목, 순서 유지: " + JSON.stringify(r));
  ok(/(후|곧 시작|분 안)/.test(r[0]) && /(후|곧 시작|분 안)/.test(r[1]), "남은 시간 표시: " + JSON.stringify(r));
  ok(/다운로드 예정/.test($("waitfree-schedule-box").textContent), "목록 제목 '다운로드 예정'");

  // Scenario 3: 남은 시간 표기(순수 함수)
  const t = (sec) => { run(`window.__t = waitfreeRemainingText(${sec});`); return w.__t; };
  ok(t(-5) === "곧 시작" && t(30) === "1분 안에" && t(90) === "1분 후" && t(45 * 60) === "45분 후" && t(2 * 3600 + 13 * 60) === "2시간 13분 후" && t(3 * 3600) === "3시간 후" && t(26 * 3600) === "1일 2시간 후", "남은 시간 표기: " + [-5, 30, 90, 2700, 7980, 10800, 93600].map(t).join(" | "));

  // Scenario 3-2: 남은 회차가 저장돼 있으면 같은 줄에 "남은 기다무 회차 · 대여권 회차 · 마지막 회차 다운로드(연도 포함)"가 붙는다. 0이거나 모르는 항목은 뺀다
  const saved = items;
  items = [
    { ...saved[0], waitfree_left: 10, rental_left: 2, last_at: "2026-12-05T12:01:10+00:00" },
    { ...saved[1], waitfree_left: 1, rental_left: 0, last_at: "2026-10-14T03:31:20+00:00" },
    { title_id: 3, title: "대여권만", next_at: "2026-10-15T03:30:00+00:00", due_at: "2026-10-15T03:31:20+00:00", waitfree_left: 0, rental_left: 3, last_at: null },
    { title_id: 4, title: "주기 모름", next_at: "2026-10-16T03:30:00+00:00", due_at: "2026-10-16T03:31:20+00:00", waitfree_left: 4, rental_left: 0, last_at: null },
    { title_id: 5, title: "아직 모름", next_at: "2026-10-17T03:30:00+00:00", due_at: "2026-10-17T03:31:20+00:00", waitfree_left: null, rental_left: null, last_at: null },
  ];
  run("window.__p = refreshWaitfreeSchedule();"); await w.__p; await idle();
  const x = rows();
  ok(x.length === 5 && /도굴왕: 엔드라인.*남은 기다무 회차 10회 · 대여권 회차 2회 · 마지막 회차 다운로드 2026년 12월 5일 21:01$/.test(x[0]), "남은 회차 한 줄: " + x[0]);
  ok(/남은 기다무 회차 1회 · 마지막 회차 다운로드 2026년 10월 14일 12:31$/.test(x[1]) && !/대여권 회차/.test(x[1]), "대여권 0이면 뺀다: " + x[1]);
  ok(/대여권만.*대여권 회차 3회$/.test(x[2]) && !/기다무 회차|마지막 회차/.test(x[2]), "기다무 0이면 뺀다: " + x[2]);
  ok(/주기 모름.*남은 기다무 회차 4회$/.test(x[3]) && !/마지막 회차|대여권 회차/.test(x[3]), "마지막 시각을 모르면 뺀다: " + x[3]);
  ok(!/기다무 회차|대여권 회차|마지막 회차/.test(x[4]), "아직 모르면 아무것도 안 붙인다: " + x[4]);
  items = saved;

  // Scenario 4: 충전 시각을 아는 작품이 없으면 안내 문구(다음 정기 다운로드가 작품마다 확인해 채운다)
  items = []; run("window.__p = refreshWaitfreeSchedule();"); await w.__p; await idle();
  ok(rows().length === 0 && /아직 충전 시각을 확인한 작품이 없습니다/.test($("waitfree-schedule-box").textContent) && /다음 정기 다운로드/.test($("waitfree-schedule-box").textContent), "빈 목록 안내: " + $("waitfree-schedule-box").textContent.replace(/\s+/g, " "));

  // Scenario 5: 끄면 바로 저장하고 목록을 숨긴다. 꺼진 상태에서는 목록을 다시 읽지 않는다(서버 요청 없음)
  $("waitfree-sync-toggle").checked = false; $("waitfree-sync-toggle").dispatchEvent(new w.Event("change")); await idle();
  ok(posts.length === 2 && posts[1] === false && hidden("waitfree-schedule-box"), "끄면 즉시 저장 + 목록 숨김");
  let fetched = 0; const realFetch = w.fetch; w.fetch = async (u, o) => { if (String(u).includes("/api/waitfree/schedule")) fetched++; return realFetch(u, o); };
  run("window.__p = refreshWaitfreeSchedule();"); await w.__p; w.fetch = realFetch;
  ok(fetched === 0, "꺼져 있으면 예정 목록을 읽지 않는다");

  // Scenario 6: 저장에 실패하면 체크를 원래대로 되돌리고 이유를 보여 준다
  const failing = makeApp({ ...routes, "/api/settings/waitfree-sync": (opts) => { if (opts && opts.method === "POST") throw new Error("저장 실패"); return { enabled: false }; } });
  await idle();
  const f$ = (id) => failing.w.document.getElementById(id);
  failing.run("window.__p = loadWaitfreeSyncSetting();"); await failing.w.__p; await idle();
  f$("waitfree-sync-toggle").checked = true; f$("waitfree-sync-toggle").dispatchEvent(new failing.w.Event("change")); await idle();
  ok(f$("waitfree-sync-toggle").checked === false && /저장 실패/.test(f$("waitfree-sync-save-result").textContent), "저장 실패 시 되돌림: " + f$("waitfree-sync-save-result").textContent);
  done();
})();
