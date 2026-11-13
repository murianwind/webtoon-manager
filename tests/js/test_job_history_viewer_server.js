// 실행 이력 목록 — 웹툰 서버(뷰어)와 주고받은 일(viewer_server)이 "웹툰 서버 연동"으로 표시되고, 스캔 주기/읽은 회차 조회/링크 조회 줄이 그대로 보이며, 실패 줄은 오류 색으로 보인다.
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const T = "2026-11-13T02:00:00+00:00";
  const history = [
    { id: 1, job_name: "viewer_server", started_at: T, finished_at: T, status: "success", log: [`${T} — 스캔 필요: 다운로드 3건 완료`, `${T} — 웹툰 서버에 스캔을 시작시켰습니다`, `${T} — 스캔이 끝났습니다 (시작 후 4분)`] },
    { id: 2, job_name: "viewer_server", started_at: "2026-11-13T01:00:00+00:00", finished_at: T, status: "error",
      log: [`${T} — 정기 아카이빙 — 웹툰 서버에 읽은 회차를 조회했습니다 (2개 작품: 성공 1, 실패 1)`, `${T} — - 작품A: 읽은 회차 3개`, `${T} — - [카카오] 작품B: 조회 실패 → 옮기지 않음`] },
  ];
  const { w, run } = makeApp({ "/api/jobs/history": history, "/api/settings/kakao-webtoons-enabled": { enabled: false }, "/api/settings/webtoon-server": { webtoon_server_url: "" } });
  await idle();
  run("loadJobHistory();"); await idle();
  const list = w.document.getElementById("job-history-list");
  const names = [...list.querySelectorAll(".job-history-name")].map((el) => el.textContent);
  ok(names.length === 2 && names.every((n) => n === "웹툰 서버 연동"), "이름 표시: " + names);
  const entries = list.querySelectorAll(".job-history-entry");
  entries[0].querySelector(".job-history-summary").click();
  ok(entries[0].textContent.includes("스캔 필요: 다운로드 3건 완료") && entries[0].textContent.includes("스캔이 끝났습니다 (시작 후 4분)") && !entries[0].querySelector(".log-error"), "스캔 주기 줄이 보이고 오류 표시는 없다");
  entries[1].querySelector(".job-history-summary").click();
  const errorLines = [...entries[1].querySelectorAll(".log-error")].map((el) => el.textContent);
  ok(entries[1].textContent.includes("읽은 회차 3개") && errorLines.length >= 1 && errorLines.some((t) => t.includes("조회 실패 → 옮기지 않음")), "조회 실패 줄은 오류 색: " + errorLines);
  const badges = [...list.querySelectorAll(".badge")].map((el) => el.className.trim());
  ok(badges[0].includes("job-success") && badges[1].includes("job-error"), "상태 배지: " + badges);
  done();
})();
