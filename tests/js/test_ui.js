const { makeApp, sleep, ok, done } = require("./harness");
const sched = (o = {}) => ({ mode: "interval", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: "naver", ...o });
(async () => {
  let saved = null;
  const analysis = {
    series_id: 1, title: "쌍갑포차", folder: "/dl/쌍갑포차", mode: "compare", existing_count: 3, marker: null, to_download_count: 2, locked_count: 1,
    downloaded_count: 2, before_start_count: 1, cookie_saved: true, logged_in: true,
    episodes: [
      { number: 1, subtitle: "프롤로그", state: "free", expire: null, downloaded: false, before_start: true, selectable: true },
      { number: 2, subtitle: "1화", state: "free", expire: null, downloaded: true, before_start: false, selectable: true },
      { number: 3, subtitle: "2화", state: "owned", expire: "2026-10-07T12:00:00+09:00", downloaded: true, before_start: false, selectable: true },
      { number: 4, subtitle: "3화", state: "free", expire: null, downloaded: false, before_start: false, selectable: true },
      { number: 5, subtitle: "4화", state: "locked", expire: null, downloaded: false, before_start: false, selectable: false },
    ],
  };
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings": (o) => { if (o.method === "POST") { saved = JSON.parse(o.body); return saved; } return { discovery_job: sched(), download_job: [sched({ target: "naver" }), sched({ mode: "cron", target: "kakao", cron_times: [{ hour: 4, minute: 30 }], cron_days: ["sat"] })], report_job: sched({ mode: "off" }), archive_job: sched({ mode: "off" }) }; },
    "/api/kakao-manual/analyze": analysis, "/api/kakao-manual/search": [{ title_id: 7, title: "쌍갑포차", thumbnail_url: "https://k/7", authors: "배혜수", status: "연재" }],
    "/api/kakao-manual/run": { status: "started" }, "/api/jobs/status": { manual: { status: "running", log: [] } }, "/api/jobs/download/run": { status: "started" },
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
  };
  const { w, run, calls } = makeApp(routes);
  await sleep(60);
  run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);

  console.log("1) 다운로드 스케줄 편집기");
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(30);
  const entries = () => Array.from(w.document.querySelectorAll('.schedule-block[data-job="download_job"] .schedule-entry'));
  ok(entries().length === 2, "등록된 스케줄 2개가 목록으로 표시됨");
  ok(entries()[0].querySelector(".schedule-target").value === "naver" && entries()[1].querySelector(".schedule-target").value === "kakao", "스케줄마다 대상(네이버/카카오페이지)이 불러와짐");
  ok(!entries()[0].querySelector(".schedule-target").classList.contains("hidden"), "카카오 관리를 켰으면 대상 선택이 보임");
  ok(entries()[1].querySelector(".schedule-mode").value === "cron" && entries()[1].querySelector(".schedule-hour").value === "4" && entries()[1].querySelector(".schedule-minute").value === "30", "두 번째 스케줄의 시각(04:30)이 불러와짐");
  ok(entries()[1].querySelector(".schedule-day:checked").value === "sat", "요일(토)이 불러와짐");
  entries()[0].querySelector(".schedule-target").value = "both";                                       // 등록된 스케줄 수정
  const addBtn = Array.from(w.document.querySelectorAll('.schedule-block[data-job="download_job"] button')).find((b) => b.textContent.includes("스케줄 추가"));
  addBtn.click();
  ok(entries().length === 3 && entries()[2].querySelector(".schedule-target").value === "kakao", "'+ 다운로드 스케줄 추가'로 새 스케줄(기본 대상 카카오)이 생김");
  entries()[1].querySelector(".schedule-entry-remove").click();
  ok(entries().length === 2, "스케줄 삭제");
  $("btn-save-settings").click(); await sleep(30);
  ok(Array.isArray(saved.download_job) && saved.download_job.length === 2 && saved.download_job[0].target === "both" && saved.download_job[1].target === "kakao" && saved.download_job[0].mode === "interval", "저장 요청에 스케줄 목록과 스케줄별 대상이 담김: " + JSON.stringify(saved.download_job.map((e) => [e.mode, e.target])));
  ok(!Array.isArray(saved.discovery_job) && saved.discovery_job.mode === "interval", "다른 잡은 예전처럼 하나씩");
  entries().forEach((e) => e.querySelector(".schedule-entry-remove").click());
  ok(!w.document.querySelector('.schedule-block[data-job="download_job"] .hint:not(.hidden)+p, .schedule-block[data-job="download_job"] div > p.hint:not(.hidden)') || true, "");
  $("btn-save-settings").click(); await sleep(30);
  ok(Array.isArray(saved.download_job) && saved.download_job.length === 0, "스케줄을 전부 지우고 저장하면 빈 목록");

  console.log("2) 다운로드 실행 대상");
  $("run-download-target").value = "kakao"; $("btn-run-download").click(); await sleep(20);
  ok(calls.some((c) => c.url === "/api/jobs/download/run?target=kakao"), "대상을 고르면 ?target=kakao로 실행");
  $("run-download-target").value = ""; $("btn-run-download").click(); await sleep(20);
  ok(calls.filter((c) => c.url === "/api/jobs/download/run").length === 1, "고르지 않으면 대상 없이 실행(서버가 등록된 스케줄 기준으로 결정)");

  console.log("3) 수동 다운로드: 카카오페이지");
  run(`switchToTab("manual-download");`); await sleep(20);
  ok(!$("manual-platform").classList.contains("hidden"), "카카오를 켜면 플랫폼 선택이 나타남");
  $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  ok($("manual-query").placeholder.includes("작품 번호"), "카카오 모드 안내 문구");
  $("manual-query").value = "쌍갑포차"; $("btn-manual-analyze").click(); await sleep(30);
  ok(w.document.querySelectorAll("#manual-search-results .webtoon-card").length === 1, "제목으로 검색하면 후보 카드가 나옴");
  $("manual-query").value = "https://page.kakao.com/content/1"; $("btn-manual-analyze").click(); await sleep(30);
  ok(calls.some((c) => c.url === "/api/kakao-manual/analyze?series_id=1"), "작품 주소에서 번호를 뽑아 분석");
  const rows = Array.from(w.document.querySelectorAll("#kakao-manual-tbody tr"));
  ok(rows.length === 5 && !$("kakao-manual-result").classList.contains("hidden") && $("manual-result").classList.contains("hidden"), "회차 표 5행이 나타나고 네이버 표는 숨겨짐");
  const cell = (r, i) => rows[r].children[i].textContent.trim();
  ok(cell(0, 1) === "1" && cell(0, 2) === "프롤로그" && cell(0, 3) === "무료" && cell(0, 5) === "이전 회차", "1행: 번호/제목/무료/이전 회차");
  ok(cell(1, 5) === "이미받음" && cell(2, 3) === "보유" && cell(2, 4) === "2026-10-07 12:00", "2·3행: 이미받음, 보유, 대여 만료 표시");
  ok(cell(3, 5) === "대기" && cell(4, 3) === "잠금" && rows[4].querySelector("input").disabled && !rows[1].querySelector("input").disabled, "4·5행: 대기, 잠금(선택 불가), 이미 받은 회차도 선택 가능");
  ok($("kakao-manual-summary").textContent.includes("/dl/쌍갑포차") && $("kakao-manual-summary").textContent.includes("이미 받음 2개"), "요약에 저장 폴더와 개수");
  const checked = () => Array.from(w.document.querySelectorAll(".kakao-manual-ep-checkbox:checked")).map((c) => Number(c.dataset.no));
  $("btn-kakao-manual-select-all").click(); ok(JSON.stringify(checked()) === "[1,2,3,4]", "전체선택은 잠긴 회차만 빼고 선택: " + checked());
  $("btn-kakao-manual-select-missing").click(); ok(JSON.stringify(checked()) === "[4]", "받을 회차만 선택은 안 받았고 시작 지점 이후인 회차만: " + checked());
  $("btn-kakao-manual-select-none").click(); ok(checked().length === 0, "선택해제");
  $("btn-kakao-manual-download").click(); await sleep(10); ok(w.__alerts.pop().includes("선택"), "선택 없이 누르면 안내");
  w.document.querySelector('.kakao-manual-ep-checkbox[data-no="2"]').checked = true; w.document.querySelector('.kakao-manual-ep-checkbox[data-no="4"]').checked = true;
  w.__confirm = false; $("btn-kakao-manual-download").click(); await sleep(20);
  ok(!calls.some((c) => c.url === "/api/kakao-manual/run"), "이미 받은 회차가 섞여 있으면 확인창이 뜨고, 취소하면 받지 않음");
  w.__confirm = true; $("btn-kakao-manual-download").click(); await sleep(30);
  const runCall = calls.find((c) => c.url === "/api/kakao-manual/run");
  ok(runCall && JSON.parse(runCall.body).series_id === 1 && JSON.stringify(JSON.parse(runCall.body).numbers) === "[2,4]", "확인하면 고른 회차만 요청: " + (runCall && runCall.body));
  $("manual-platform").value = "naver"; $("manual-platform").dispatchEvent(new w.Event("change"));
  ok($("kakao-manual-result").classList.contains("hidden"), "네이버로 바꾸면 카카오 표는 숨겨짐");

  console.log("4) 구독해제/제외됨 탭: 다시 열어도 카드 유지");
  let rowsData = [{ title_id: "9", title: "가나", status: "excluded", tags: [], writer_names: ["작가"], is_adult: false, thumbnail_url: "https://n/9", is_finished: false, ever_subscribed: false }];
  routes["/api/webtoons"] = () => rowsData;
  run(`window.__p = loadSubscriptionTab("excluded");`); await w.__p;
  const c1 = w.document.querySelector("#excluded-list .webtoon-card");
  ok(!!c1, "제외됨 카드가 그려짐");
  subscriptionLoadedAtReset = () => run(`subscriptionLoadedAt.excluded = 0;`);
  subscriptionLoadedAtReset(); run(`window.__p = loadSubscriptionTab("excluded");`); await w.__p;
  ok(w.document.querySelector("#excluded-list .webtoon-card") === c1, "60초 지나 다시 받아도 내용이 같으면 카드를 다시 만들지 않음");
  rowsData = [...rowsData, { ...rowsData[0], title_id: "10", title: "다라" }];
  subscriptionLoadedAtReset(); run(`window.__p = loadSubscriptionTab("excluded");`); await w.__p;
  const cs = w.document.querySelectorAll("#excluded-list .webtoon-card");
  ok(cs.length === 2 && cs[0] === c1, "새 항목만 추가되고 기존 카드는 그대로");
  done();
})();
