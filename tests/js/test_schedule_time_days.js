// 실행 스케줄 화면 — 요일은 "시각마다" 고른다: 시각 줄(시·분) 옆에 월~일 칩이 있고, 아무 요일도 안 고르면 매일. 저장할 때 시각마다 days로 보낸다(공통 cron_days는 더 이상 보내지 않는다).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let saved = null;
  const sched = (o = {}) => ({ mode: "interval", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0, days: [] }], target: "naver", ...o });
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings": (o) => {
      if (o.method === "POST") { saved = JSON.parse(o.body); return saved; }
      return {
        discovery_job: sched({ mode: "cron", cron_times: [{ hour: 7, minute: 0, days: ["mon", "fri"] }, { hour: 9, minute: 30, days: [] }] }),
        download_job: [sched({ mode: "cron", target: "kakao", cron_times: [{ hour: 4, minute: 30, days: ["sat", "sun"] }] })],
        report_job: sched({ mode: "off" }), archive_job: sched({ mode: "cron", cron_times: [{ hour: 5, minute: 0, days: ["tue"] }] }),
      };
    },
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(40);
  const block = (job) => w.document.querySelector(`.schedule-block[data-job="${job}"] .schedule-controls`);
  const rows = (job) => [...block(job).querySelectorAll(".schedule-time-row")];
  const checkedDays = (row) => [...row.querySelectorAll(".schedule-day:checked")].map((c) => c.value);

  // Scenario 1: 시각 줄마다 자기 요일 칩 7개가 있고, 저장된 요일만 체크되어 불러와진다(신작 스캔/다운로드/아카이빙 모두)
  const disc = rows("discovery_job");
  ok(disc.length === 2 && disc.every((r) => r.querySelectorAll(".schedule-day").length === 7), "신작 스캔: 시각 줄마다 요일 칩 7개");
  ok(JSON.stringify(checkedDays(disc[0])) === '["mon","fri"]' && checkedDays(disc[1]).length === 0, "시각마다 요일이 따로 불러와짐: " + JSON.stringify(disc.map(checkedDays)));
  const dl = w.document.querySelector('.schedule-block[data-job="download_job"] .schedule-entry');
  ok(JSON.stringify([...dl.querySelectorAll(".schedule-time-row")].map(checkedDays)) === '[["sat","sun"]]', "다운로드 스케줄도 시각마다 요일");
  ok(JSON.stringify(rows("archive_job").map(checkedDays)) === '[["tue"]]', "아카이빙도 시각마다 요일");
  // 공통 요일 줄은 없다 — 요일 칩은 시각 줄 안에만 있다. 설명은 한 번
  ok(block("discovery_job").querySelectorAll(".day-checkbox-group").length === 0 && /안 고르면 매일/.test(block("discovery_job").textContent), "공통 요일 줄은 없고, '안 고르면 매일' 안내만 있다");
  ok([...disc[0].children].some((el) => el.querySelector && el.querySelector(".schedule-day")) && disc[0].querySelector(".schedule-time-remove"), "칩은 시각 줄 안에 있고 삭제 버튼도 같은 줄");

  // Scenario 2: 시각을 추가하면 그 줄만의 요일 칩이 생기고(처음엔 전부 해제 = 매일), 다른 줄과 독립이다
  const addBtn = [...block("discovery_job").querySelectorAll("button")].find((b) => /시각 추가/.test(b.textContent)); addBtn.click();
  const after = rows("discovery_job");
  ok(after.length === 3 && checkedDays(after[2]).length === 0, "새 시각 줄은 요일 해제(매일)로 시작");
  after[2].querySelector('.schedule-day[value="wed"]').checked = true;
  ok(JSON.stringify(after.map(checkedDays)) === '[["mon","fri"],[],["wed"]]', "줄마다 독립: " + JSON.stringify(after.map(checkedDays)));

  // Scenario 3: 저장하면 시각마다 days로 보낸다(공통 cron_days 없음). 시각을 지워도 나머지 줄의 요일은 그대로
  after[1].querySelector(".schedule-time-remove").click();
  w.document.getElementById("btn-save-settings").click(); await sleep(80);
  const sent = saved.discovery_job;
  ok(JSON.stringify(sent.cron_times) === JSON.stringify([{ hour: 7, minute: 0, days: ["mon", "fri"] }, { hour: 3, minute: 0, days: ["wed"] }]), "신작 스캔 저장 내용: " + JSON.stringify(sent.cron_times));
  ok(!("cron_days" in sent) && !("cron_days" in saved.archive_job) && saved.download_job.every((e) => !("cron_days" in e)), "공통 cron_days는 보내지 않는다");
  ok(JSON.stringify(saved.download_job[0].cron_times) === '[{"hour":4,"minute":30,"days":["sat","sun"]}]' && JSON.stringify(saved.archive_job.cron_times) === '[{"hour":5,"minute":0,"days":["tue"]}]', "다운로드/아카이빙 저장 내용");

  // Scenario 4: 예전 응답(시각에 days 없음)도 깨지지 않고 매일로 보인다
  const old = makeApp({ ...routes, "/api/settings": () => ({ discovery_job: { mode: "cron", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: "naver" }, download_job: [], report_job: sched({ mode: "off" }), archive_job: sched({ mode: "off" }) }) });
  await sleep(60); old.run("window.__p = loadSettingsPage();"); await old.w.__p; await sleep(40);
  const oldRow = old.w.document.querySelector('.schedule-block[data-job="discovery_job"] .schedule-time-row');
  ok(oldRow && oldRow.querySelectorAll(".schedule-day:checked").length === 0, "days가 없는 시각은 매일(체크 없음)로 표시");
  done();
})();
