// 설정 > 실행 스케줄: "기다무 충전 시각에 맞춰 자동 다운로드" 옵션(켜기/끄기, 즉시 저장)과, 켜면 그 아래에 다운로드 예정 시각과 웹툰 제목 목록이 보인다.
const { makeApp, sleep, ok, done } = require("./harness");
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
  const { w, run } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  const hidden = (id) => $(id).classList.contains("hidden");
  const rows = () => [...w.document.querySelectorAll("#waitfree-schedule-list li")].map((li) => li.textContent.replace(/\s+/g, " ").trim());

  // Scenario 1: 처음(옵션 꺼짐) — 체크 해제, 설명과 예정 목록은 숨김. 옵션은 "카카오웹툰 관리" 카드 안(대여권 옵션 아래)에 있다
  run("window.__p = loadWaitfreeSyncSetting();"); await w.__p; await sleep(40);
  ok($("waitfree-sync-toggle").checked === false && hidden("waitfree-schedule-box"), "꺼짐: 체크 해제, 예정 목록 숨김");
  const card = $("settings-card-kakao"), rental = $("kakao-rental-auto-group");
  ok(card.contains($("waitfree-sync-toggle")) && !$("settings-card-run").contains($("waitfree-sync-toggle")) && !!(rental.compareDocumentPosition($("waitfree-sync-toggle")) & w.Node.DOCUMENT_POSITION_FOLLOWING), "카카오웹툰 관리 카드 안, 대여권 옵션 아래에 있다");
  ok(/정기 다운로드는 충전 시각 전인 기다무 작품을 건너뛰/.test($("waitfree-sync-group").textContent) && /수동 실행은/.test($("waitfree-sync-group").textContent), "옵션 설명: 정기 다운로드가 건너뜀, 수동 실행은 그대로");

  // Scenario 2: 켜면 바로 저장(요청 한 번)하고 그 아래에 "다운로드 예정" 목록이 시각 순(서버가 준 순서)으로 나온다 — 시각(한국시간)과 웹툰 제목, 남은 시간
  $("waitfree-sync-toggle").checked = true; $("waitfree-sync-toggle").dispatchEvent(new w.Event("change")); await sleep(80);
  ok(posts.length === 1 && posts[0] === true && $("waitfree-sync-save-result").textContent === "저장했습니다.", "켜면 즉시 저장: " + posts.length + " / " + $("waitfree-sync-save-result").textContent);
  ok(!hidden("waitfree-schedule-box"), "켜면 예정 목록이 보인다");
  const r = rows();
  ok(r.length === 2 && /10월 13일 21:01/.test(r[0]) && /도굴왕: 엔드라인/.test(r[0]) && /10월 14일 12:31/.test(r[1]) && /나 혼자만 레벨업/.test(r[1]), "한국시간 시각 + 제목, 순서 유지: " + JSON.stringify(r));
  ok(/(후|곧 시작|분 안)/.test(r[0]) && /(후|곧 시작|분 안)/.test(r[1]), "남은 시간 표시: " + JSON.stringify(r));
  ok(/다운로드 예정/.test($("waitfree-schedule-box").textContent), "목록 제목 '다운로드 예정'");

  // Scenario 3: 남은 시간 표기(순수 함수)
  const t = (sec) => { run(`window.__t = waitfreeRemainingText(${sec});`); return w.__t; };
  ok(t(-5) === "곧 시작" && t(30) === "1분 안에" && t(90) === "1분 후" && t(45 * 60) === "45분 후" && t(2 * 3600 + 13 * 60) === "2시간 13분 후" && t(3 * 3600) === "3시간 후" && t(26 * 3600) === "1일 2시간 후", "남은 시간 표기: " + [-5, 30, 90, 2700, 7980, 10800, 93600].map(t).join(" | "));

  // Scenario 4: 충전 시각을 아는 작품이 없으면 안내 문구(다음 정기 다운로드가 작품마다 확인해 채운다)
  items = []; run("window.__p = refreshWaitfreeSchedule();"); await w.__p; await sleep(40);
  ok(rows().length === 0 && /아직 충전 시각을 확인한 작품이 없습니다/.test($("waitfree-schedule-box").textContent) && /다음 정기 다운로드/.test($("waitfree-schedule-box").textContent), "빈 목록 안내: " + $("waitfree-schedule-box").textContent.replace(/\s+/g, " "));

  // Scenario 5: 끄면 바로 저장하고 목록을 숨긴다. 꺼진 상태에서는 목록을 다시 읽지 않는다(서버 요청 없음)
  $("waitfree-sync-toggle").checked = false; $("waitfree-sync-toggle").dispatchEvent(new w.Event("change")); await sleep(80);
  ok(posts.length === 2 && posts[1] === false && hidden("waitfree-schedule-box"), "끄면 즉시 저장 + 목록 숨김");
  let fetched = 0; const realFetch = w.fetch; w.fetch = async (u, o) => { if (String(u).includes("/api/waitfree/schedule")) fetched++; return realFetch(u, o); };
  run("window.__p = refreshWaitfreeSchedule();"); await w.__p; w.fetch = realFetch;
  ok(fetched === 0, "꺼져 있으면 예정 목록을 읽지 않는다");

  // Scenario 6: 저장에 실패하면 체크를 원래대로 되돌리고 이유를 보여 준다
  const failing = makeApp({ ...routes, "/api/settings/waitfree-sync": (opts) => { if (opts && opts.method === "POST") throw new Error("저장 실패"); return { enabled: false }; } });
  await sleep(60);
  const f$ = (id) => failing.w.document.getElementById(id);
  failing.run("window.__p = loadWaitfreeSyncSetting();"); await failing.w.__p; await sleep(40);
  f$("waitfree-sync-toggle").checked = true; f$("waitfree-sync-toggle").dispatchEvent(new failing.w.Event("change")); await sleep(80);
  ok(f$("waitfree-sync-toggle").checked === false && /저장 실패/.test(f$("waitfree-sync-save-result").textContent), "저장 실패 시 되돌림: " + f$("waitfree-sync-save-result").textContent);
  done();
})();
