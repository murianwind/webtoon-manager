// 설정 화면 정리 — ① 다운로드 폴더/다운로드 속도 조절/신작 스캔/다운로드/아카이빙을 카드 하나("다운로드 · 실행 스케줄") 안의 탭으로 모은다
// ② 웹툰 뷰어 서버 주소와 이 앱의 외부 접속 주소를 카드 하나("연동 주소", 웹훅이 있어야 보임)로 모은다 ③ 기다무 충전 시각 다운로드 옵션은 "카카오웹툰 관리" 카드로 옮긴다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const cron = (h, m = 0, days = []) => ({ mode: "cron", interval_minutes: 60, cron_times: [{ hour: h, minute: m, days }], target: "naver" });
  let saved = null, webhook = false;
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "http://192.168.0.49:25600" },
    "/api/settings": (o) => {
      if (o.method === "POST") { saved = JSON.parse(o.body); return saved; }
      return { discovery_job: cron(7, 0, ["mon"]), download_job: [{ ...cron(4, 30), target: "kakao" }], archive_job: cron(5, 0) };
    },
    "/api/settings/discord": () => ({ webhook_url_set: webhook, bot_token_set: false, notify_channel_id: "", bot_ready: false }),
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  const card = $("settings-card-run");
  const tabs = () => [...card.querySelectorAll(".card-tabs .card-tab")];
  const panels = () => [...card.querySelectorAll(".card-tab-panel")];
  const visiblePanels = () => panels().filter((p) => !p.classList.contains("hidden")).map((p) => p.dataset.panel);
  const click = (label) => tabs().find((t) => t.textContent.trim() === label).click();
  const shown = (el) => !el.classList.contains("hidden");

  // Scenario 1: 카드 순서 — 다운로드·실행 스케줄(탭) → 카카오웹툰 관리 → 디스코드 → 연동 주소 → 다운로드 리포트. 예전의 따로따로 카드(다운로드 폴더/속도 조절/실행 스케줄)는 없다
  const ids = [...w.document.querySelectorAll("#page-settings .settings-card[id]")].map((e) => e.id);
  const want = ["settings-card-run", "settings-card-kakao", "settings-card-discord", "settings-card-links", "settings-card-report"];
  ok(JSON.stringify(ids.filter((i) => want.includes(i))) === JSON.stringify(want), "카드 순서: " + JSON.stringify(ids));
  ok(!$("settings-card-download-roots") && !$("settings-card-pacing") && !$("settings-card-schedule"), "예전 카드(다운로드 폴더/속도 조절/실행 스케줄)는 없다");
  const h2s = [...w.document.querySelectorAll("#page-settings .settings-card > h2")].map((h) => h.textContent.trim());
  ok(!h2s.includes("다운로드 폴더") && !h2s.includes("다운로드 속도 조절") && !h2s.includes("실행 스케줄"), "따로 있던 제목이 남아 있지 않다: " + JSON.stringify(h2s));

  // Scenario 2: 탭 — 다섯 개, 처음엔 첫 탭(다운로드 폴더)만 보인다
  ok(card.querySelector("h2").textContent.trim() === "다운로드 · 실행 스케줄", "카드 제목");
  ok(JSON.stringify(tabs().map((t) => t.textContent.trim())) === JSON.stringify(["다운로드 폴더", "다운로드 속도 조절", "신작 스캔", "다운로드", "아카이빙"]), "탭 순서/이름: " + tabs().map((t) => t.textContent.trim()));
  ok(card.querySelector(".card-tabs").getAttribute("role") === "tablist" && tabs().every((t) => t.getAttribute("role") === "tab"), "접근성: tablist/tab");
  ok(tabs()[0].classList.contains("active") && tabs()[0].getAttribute("aria-selected") === "true" && tabs().slice(1).every((t) => !t.classList.contains("active") && t.getAttribute("aria-selected") === "false"), "첫 탭만 선택됨");
  ok(JSON.stringify(visiblePanels()) === '["roots"]' && panels().length === 5, "첫 패널만 보임: " + JSON.stringify(visiblePanels()));

  // Scenario 3: 패널 내용 — 예전 카드의 항목이 그대로 각 탭 안에 있다(아이디 그대로)
  const inPanel = (name, sel) => !!card.querySelector(`.card-tab-panel[data-panel="${name}"] ${sel}`);
  ok(inPanel("roots", "#naver-download-root-picker") && inPanel("roots", "#kakao-download-root-group") && inPanel("roots", "#btn-download-roots-save"), "다운로드 폴더 탭");
  ok(inPanel("pacing", "#pacing-cap") && inPanel("pacing", "#pacing-rest") && inPanel("pacing", "#btn-save-pacing") && inPanel("pacing", "#btn-reset-pacing"), "다운로드 속도 조절 탭");
  ok(inPanel("discovery_job", '.schedule-block[data-job="discovery_job"] .schedule-time-row') && inPanel("download_job", '.schedule-block[data-job="download_job"] .schedule-entry') && inPanel("archive_job", '.schedule-block[data-job="archive_job"] .schedule-time-row'), "스케줄 탭 3개에 불러온 스케줄이 들어 있다(숨겨진 탭에도 미리 그려진다)");
  ok(/최소 10분 뒤/.test(card.querySelector('.card-tab-panel[data-panel="archive_job"]').textContent) && /여러 개 등록/.test(card.querySelector('.card-tab-panel[data-panel="download_job"]').textContent), "아카이빙 탭에 10분 규칙, 다운로드 탭에 여러 스케줄 안내");
  ok(!card.querySelector("#waitfree-sync-group"), "기다무 충전 시각 옵션은 이 카드에 없다");

  // Scenario 4: 탭 전환 — 누른 탭만 선택되고 그 패널만 보인다. 스케줄 탭(신작 스캔/다운로드/아카이빙)에서만 공용 저장 줄이 보이고, 폴더/속도 조절은 각자의 저장 버튼을 쓴다
  const footer = $("schedule-save-footer");
  ok(footer && card.contains(footer) && !shown(footer) && footer.contains($("btn-save-settings")) && footer.contains($("settings-save-result")), "저장 줄은 스케줄 탭에서만(처음엔 숨김)");
  click("다운로드 속도 조절");
  ok(JSON.stringify(visiblePanels()) === '["pacing"]' && tabs()[1].classList.contains("active") && tabs()[1].getAttribute("aria-selected") === "true" && !tabs()[0].classList.contains("active") && !shown(footer), "속도 조절 탭: 패널 전환, 저장 줄 숨김");
  click("신작 스캔"); ok(JSON.stringify(visiblePanels()) === '["discovery_job"]' && shown(footer), "신작 스캔 탭: 저장 줄 보임");
  click("다운로드"); ok(JSON.stringify(visiblePanels()) === '["download_job"]' && shown(footer), "다운로드 탭");
  click("아카이빙"); ok(JSON.stringify(visiblePanels()) === '["archive_job"]' && shown(footer), "아카이빙 탭");
  click("다운로드 폴더"); ok(JSON.stringify(visiblePanels()) === '["roots"]' && !shown(footer), "다운로드 폴더 탭으로 돌아오면 저장 줄 숨김");

  // Scenario 5: 설정을 다시 불러와도 보고 있던 탭은 그대로, 저장은 세 스케줄을 모두 보낸다(숨겨진 탭의 값도)
  click("아카이빙"); run("window.__p = loadSettingsPage();"); await w.__p; await sleep(40);
  ok(JSON.stringify(visiblePanels()) === '["archive_job"]', "다시 불러와도 탭 유지");
  $("btn-save-settings").click(); await sleep(80);
  ok(saved && Object.keys(saved).sort().join() === "archive_job,discovery_job,download_job" && saved.discovery_job.cron_times[0].days[0] === "mon" && saved.download_job[0].target === "kakao", "숨겨진 탭의 스케줄까지 함께 저장: " + JSON.stringify(saved && Object.keys(saved)));
  ok($("settings-save-result").textContent === "저장했습니다.", "저장 결과");

  // Scenario 6: 기다무 충전 시각 다운로드 옵션은 "카카오웹툰 관리" 카드 안에 있다
  const kakao = $("settings-card-kakao");
  ok(kakao.contains($("waitfree-sync-group")) && kakao.contains($("waitfree-sync-toggle")) && kakao.contains($("waitfree-schedule-box")), "카카오웹툰 관리 카드 안");
  ok(!!(kakao.querySelector("#kakao-rental-auto-group").compareDocumentPosition($("waitfree-sync-group")) & w.Node.DOCUMENT_POSITION_FOLLOWING), "대여권 옵션 아래에 있다");

  // Scenario 7: 연동 주소 카드 — 뷰어 서버 주소(+스캔 설정)와 이 앱의 외부 접속 주소가 한 카드에 있다. 디스코드 링크(바로가기/목록 제외)에 쓰는 주소라 리포트 카드처럼 웹훅이 있어야 보인다. 리포트 카드에는 더 없다
  const links = $("settings-card-links"), report = $("settings-card-report");
  ok(links.querySelector("h2").textContent.trim() === "연동 주소", "연동 주소 카드 제목");
  ok(["webtoon-server-url", "btn-save-webtoon-server-url", "viewer-scan-toggle", "viewer-scan-token", "viewer-scan-max-wait", "btn-save-viewer-scan", "app-public-base-url", "btn-save-app-public-base-url"].every((id) => links.contains($(id))), "두 주소와 뷰어 스캔 설정이 한 카드에");
  ok(!shown(links) && !shown(report), "웹훅이 없으면 연동 주소/리포트 카드 모두 숨김");
  ok(["webtoon-server-url", "app-public-base-url", "viewer-scan-toggle"].every((id) => !report.contains($(id))), "리포트 카드에는 주소/스캔 설정이 없다");
  ok(report.contains($("report-quiet-group")) && report.contains($("report-batch-minutes")) && report.contains($("report-unregistered-toggle")), "리포트 카드에는 발송하지 않는 시간/모으는 시간/새 에피소드 섹션");
  ok($("webtoon-server-url").value === "http://192.168.0.49:25600", "뷰어 서버 주소는 그대로 불러와짐(숨겨져 있어도 값은 유지)");
  webhook = true; run("window.__p = loadDiscordSettings();"); await w.__p; await sleep(40);
  ok(shown(links) && shown(report), "웹훅을 저장하면 연동 주소/리포트 카드가 나타난다");
  done();
})();
