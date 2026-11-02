// 설정 > 다운로드 리포트: 발송 시각 스케줄은 없어지고 "발송하지 않는 시간"(시작~끝 + 요일 칩, 여러 개)과 "모으는 시간(N분)"만 설정한다.
// 같은 카드의 웹툰 뷰어 서버 항목에는 "다운로드 뒤 뷰어 스캔"(켜기, 연동 토큰, 최대 대기 시간) 설정이 있다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const quiet = (sh, sm, eh, em, days = []) => ({ start_hour: sh, start_minute: sm, end_hour: eh, end_minute: em, days });
  let report = { quiet_hours: [quiet(23, 0, 6, 0, ["mon", "tue"]), quiet(13, 30, 14, 0)], batch_minutes: 15 };
  let scan = { enabled: false, token_set: true, max_wait_minutes: 45, ready: false };
  let serverUrl = "http://viewer";
  const posts = { report: [], scan: [] }; let reportError = null, scanError = null;
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": (o) => { if (o && o.method === "POST") serverUrl = JSON.parse(o.body).webtoon_server_url; return { webtoon_server_url: serverUrl }; },
    "/api/settings": { discovery_job: { mode: "off", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0, days: [] }], target: "naver" }, download_job: [], archive_job: { mode: "off", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0, days: [] }], target: "naver" } },
    "/api/settings/report": (o) => { if (o && o.method === "POST") { if (reportError) throw new Error(reportError); report = JSON.parse(o.body); posts.report.push(report); } return report; },
    "/api/settings/viewer-scan": (o) => { if (o && o.method === "POST") { if (scanError) throw new Error(scanError); const b = JSON.parse(o.body); posts.scan.push(b); scan = { ...scan, enabled: b.enabled, max_wait_minutes: b.max_wait_minutes, token_set: b.clear_token ? false : (b.token ? true : scan.token_set) }; } return scan; },
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(60);
  const rows = () => [...w.document.querySelectorAll("#report-quiet-list .report-quiet-row")];
  const val = (row, cls) => Number(row.querySelector(cls).value);
  const days = (row) => [...row.querySelectorAll(".quiet-day:checked")].map((c) => c.value);

  // Scenario 1: 옛 "발송 시각" 스케줄 UI는 없다. 저장된 발송하지 않는 시간(행마다 시작~끝, 요일 칩)과 모으는 시간이 불러와진다
  ok(!w.document.querySelector('.schedule-block[data-job="report_job"]') && !$("btn-save-report-schedule"), "발송 시각 스케줄 UI 제거");
  const r = rows();
  ok(r.length === 2 && val(r[0], ".quiet-start-hour") === 23 && val(r[0], ".quiet-start-minute") === 0 && val(r[0], ".quiet-end-hour") === 6 && val(r[0], ".quiet-end-minute") === 0, "첫 시간대 23:00~06:00 불러옴");
  ok(JSON.stringify(days(r[0])) === '["mon","tue"]' && days(r[1]).length === 0 && val(r[1], ".quiet-start-minute") === 30, "요일 칩과 두 번째 시간대(13:30~14:00, 매일)");
  ok(r[0].querySelectorAll(".quiet-day").length === 7 && !!r[0].querySelector(".quiet-remove"), "행마다 요일 칩 7개와 삭제 버튼");
  ok($("report-batch-minutes").value === "15", "모으는 시간 15분");
  ok(/시작하는 날 기준/.test($("settings-card-report").textContent) && /안 고르면 매일/.test($("settings-card-report").textContent) && /겹치는 시간대는 저장할 수 없/.test($("settings-card-report").textContent), "안내 문구(시작하는 날 기준, 안 고르면 매일, 겹침 불가)");

  // Scenario 2: 시간대 추가(기본 23:00~06:00, 매일)와 삭제 — 전부 지워도 된다(항상 발송). 지정이 없으면 안내가 보인다
  [...$("settings-card-report").querySelectorAll("button")].find((b) => /시간대 추가/.test(b.textContent)).click();
  ok(rows().length === 3 && val(rows()[2], ".quiet-start-hour") === 23 && val(rows()[2], ".quiet-end-hour") === 6 && days(rows()[2]).length === 0, "새 시간대는 23:00~06:00 매일로 시작");
  rows()[2].querySelectorAll(".quiet-day")[5].checked = true;
  ok(JSON.stringify(days(rows()[2])) === '["sat"]' && days(rows()[0]).length === 2, "행마다 요일 독립");
  rows().forEach((row) => row.querySelector(".quiet-remove").click());
  ok(rows().length === 0 && !$("report-quiet-empty").classList.contains("hidden") && /항상 바로 보냅니다/.test($("report-quiet-empty").textContent), "전부 지우면 '항상 바로 보냅니다' 안내");

  // Scenario 3: 저장 — 시간대(시작/끝/요일)와 모으는 시간을 보낸다. 성공 메시지
  [...$("settings-card-report").querySelectorAll("button")].find((b) => /시간대 추가/.test(b.textContent)).click();
  rows()[0].querySelector(".quiet-start-hour").value = "22"; rows()[0].querySelector(".quiet-end-hour").value = "7"; rows()[0].querySelectorAll(".quiet-day")[0].checked = true;
  $("report-batch-minutes").value = "20";
  $("btn-save-report-settings").click(); await sleep(80);
  ok(JSON.stringify(posts.report[0]) === JSON.stringify({ quiet_hours: [quiet(22, 0, 7, 0, ["mon"])], batch_minutes: 20 }), "저장 내용: " + JSON.stringify(posts.report[0]));
  ok($("report-settings-save-result").textContent === "저장했습니다.", "성공 메시지");

  // Scenario 4: 겹침 같은 서버 오류는 메시지로 보이고 입력한 행은 그대로 남는다
  reportError = "발송하지 않는 시간 23:00~06:00(매일)와 05:00~07:00(월)이 겹칩니다. 겹치지 않게 고쳐 주세요.";
  $("btn-save-report-settings").click(); await sleep(80);
  ok(/겹칩니다/.test($("report-settings-save-result").textContent) && rows().length === 1, "겹침 오류 표시 + 행 유지: " + $("report-settings-save-result").textContent);
  reportError = null;

  // Scenario 5: 뷰어 스캔 설정 — 불러오기(토큰 값은 안 보이고 저장돼 있다는 표시만), 켜고 새 토큰/대기 시간 저장, 토큰 지우기, 오류
  ok($("viewer-scan-toggle").checked === false && $("viewer-scan-token").value === "" && /저장됨/.test($("viewer-scan-token").placeholder) && $("viewer-scan-max-wait").value === "45", "스캔 설정 불러옴(토큰은 표시만)");
  ok(/15~30분/.test($("settings-card-links").textContent) && /오래 걸/.test($("settings-card-links").textContent), "스캔이 오래 걸릴 수 있다는 안내(연동 주소 카드)");
  const scanText = $("viewer-scan-toggle").closest(".viewer-scan-box").textContent.replace(/\s+/g, " ");
  ok(/다운로드/.test(scanText) && /아카이빙/.test(scanText) && /수동 다운로드/.test(scanText) && /폴더 일괄 이동/.test(scanText), "스캔을 시키는 경우(다운로드, 수동 다운로드, 아카이빙, 폴더 일괄 이동)를 안내: " + scanText.slice(0, 200));
  ok(/한 번에 하나/.test(scanText) || /겹치지 않/.test(scanText), "스캔은 겹치지 않고 하나씩 돈다는 안내");
  $("viewer-scan-toggle").checked = true; $("viewer-scan-token").value = "새토큰"; $("viewer-scan-max-wait").value = "60";
  $("btn-save-viewer-scan").click(); await sleep(80);
  ok(JSON.stringify(posts.scan[0]) === JSON.stringify({ enabled: true, token: "새토큰", clear_token: false, max_wait_minutes: 60 }) && $("viewer-scan-token").value === "" && $("viewer-scan-save-result").textContent === "저장했습니다.", "스캔 설정 저장: " + JSON.stringify(posts.scan[0]));
  $("btn-save-viewer-scan").click(); await sleep(80);
  ok(posts.scan[1].token === null && posts.scan[1].clear_token === false, "토큰을 안 적으면 기존 토큰 유지(null)");
  $("btn-clear-viewer-scan-token").click(); await sleep(80);
  ok(posts.scan[2].clear_token === true && /없음|입력/.test($("viewer-scan-token").placeholder), "토큰 지우기");
  scanError = "스캔 대기 시간은 1~240분이어야 합니다."; $("btn-save-viewer-scan").click(); await sleep(80);
  ok(/1~240분/.test($("viewer-scan-save-result").textContent), "스캔 설정 오류 표시");
  // Scenario 6: 뷰어 스캔 설정(박스)은 뷰어 서버 주소가 저장돼 있을 때만 보인다 — 주소를 저장하면 나타나고, 지우면 사라진다. 숨겨져도 저장된 스캔 설정은 그대로
  const box = $("viewer-scan-toggle").closest(".viewer-scan-box");
  ok(!box.classList.contains("hidden"), "주소가 저장돼 있으면 스캔 설정이 보임");
  $("webtoon-server-url").value = ""; $("btn-save-webtoon-server-url").click(); await sleep(60);
  ok(box.classList.contains("hidden") && $("webtoon-server-save-result").textContent === "저장했습니다.", "주소를 지우고 저장하면 스캔 설정이 숨겨짐");
  $("webtoon-server-url").value = "http://192.168.0.49:25600"; $("btn-save-webtoon-server-url").click(); await sleep(60);
  ok(!box.classList.contains("hidden"), "주소를 저장하면 스캔 설정이 나타남");
  $("webtoon-server-url").value = ""; $("webtoon-server-url").dispatchEvent(new w.Event("input"));
  ok(!box.classList.contains("hidden"), "주소 입력칸을 지우는 중이어도(저장 전) 바뀌지 않는다 — 저장된 주소 기준");
  $("webtoon-server-url").value = "http://typed-but-not-saved"; $("webtoon-server-url").dispatchEvent(new w.Event("input"));
  ok(!box.classList.contains("hidden"), "입력만 하고 저장하기 전에는 바뀌지 않는다(저장된 주소 기준)");
  serverUrl = ""; run("window.__p = loadWebtoonServerUrl();"); await w.__p; await sleep(30);
  ok(box.classList.contains("hidden"), "저장된 주소가 없으면(설정 화면을 열 때) 숨겨짐");
  done();
})();
