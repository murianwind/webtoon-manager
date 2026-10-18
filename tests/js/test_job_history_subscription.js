// 실행 이력 목록 — 구독 이력(subscription)이 "구독/작가 등록"으로 표시되고, 작가 등록 결과 줄이 그대로 보인다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const history = [{ id: 1, job_name: "subscription", started_at: "2026-10-18T01:00:00+00:00", finished_at: "2026-10-18T01:00:00+00:00", status: "success",
    log: ["2026-10-18T01:00:00+00:00 — [카카오] 작품 — 구독", "2026-10-18T01:00:00+00:00 — [카카오] 작품 — 원작자 등록: 원작가(신규)"] }];
  const { w, run } = makeApp({ "/api/jobs/history": history, "/api/settings/kakao-webtoons-enabled": { enabled: false }, "/api/settings/webtoon-server": { webtoon_server_url: "" } });
  await sleep(60);
  run("loadJobHistory();");
  await sleep(60);
  const list = w.document.getElementById("job-history-list");
  ok(list.querySelector(".job-history-name").textContent === "구독/작가 등록", "구독 이력의 이름 표시: " + list.querySelector(".job-history-name").textContent);
  list.querySelector(".job-history-summary").click();
  ok(list.textContent.includes("원작자 등록: 원작가(신규)"), "작가 등록 결과 줄이 보임");
  done();
})();
