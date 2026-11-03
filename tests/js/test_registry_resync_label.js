// "지금 전체 재동기화"(작가/태그 관리) 버튼 옆 상태 문구 — 몇 분 걸릴 수 있는 작업(카카오 40개씩 쉬어 가며 확인)이라 실행 중에는 최신 진행 상황을 함께 보여 준다.
// 로그 한 줄은 "시각 — 내용" 형태라 시각은 떼고 내용만. 끝나면 "완료됨", 오류면 안내. (다른 화면의 같은 함수 사용처는 예전처럼 고정 문구)
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  let registry = { status: "running", log: [] };
  const { w, calls, run } = makeApp({ "/api/registry/resync": {}, "/api/jobs/status": () => ({ registry }), "/api/authors/interested": [], "/api/authors/candidates": [], "/api/tags/catalog": [], "/api/watched-tags": [] });
  await idle();
  const label = () => w.document.getElementById("registry-resync-status").textContent;
  const poll = async () => { run("refreshRegistryJobStatuses();"); await idle(); };

  // Scenario 1: 실행 중 — 로그가 없으면 "재동기화 중...", 있으면 마지막 줄의 내용(시각 제외)을 덧붙인다
  await poll(); ok(label() === "재동기화 중...", "로그 없음: " + label());
  registry = { status: "running", log: ["2026-11-03T01:00:00+00:00 — 작가/태그 재동기화 시작", "2026-11-03T01:00:30+00:00 — 카카오 구독해제/제외됨 상태 확인 40/105개 진행"] };
  await poll(); ok(label() === "재동기화 중... 카카오 구독해제/제외됨 상태 확인 40/105개 진행", "마지막 줄(시각 제외): " + label());
  registry = { status: "running", log: ["시각 없는 줄"] }; await poll(); ok(label() === "재동기화 중... 시각 없는 줄", "시각 구분이 없으면 줄 그대로: " + label());

  // Scenario 2: 끝나면 "완료됨"(진행 문구는 사라진다), 오류면 안내
  registry = { status: "success", log: ["2026-11-03T01:05:00+00:00 — 끝"] }; await poll(); ok(label() === "완료됨", "완료: " + label());
  registry = { status: "error", log: [] }; await poll(); ok(label().startsWith("오류 발생"), "오류: " + label());

  // Scenario 3: 다른 화면에서 같은 함수를 부르는 곳은 진행 문구 없이 고정 문구(옵션을 켠 곳만 진행 상황을 보여 준다)
  const el = w.document.createElement("span"); el.id = "other-status"; w.document.body.appendChild(el);
  w.__st = { status: "running", log: ["2026-11-03T01:00:00+00:00 — 로그 줄"] };
  run("applyJobStatusToLabel(__st, 'other-status', '실행 중...');"); ok(el.textContent === "실행 중...", "기본은 고정 문구: " + el.textContent);
  run("applyJobStatusToLabel(__st, 'other-status', '실행 중...', true);"); ok(el.textContent === "실행 중... 로그 줄", "옵션을 켜면 진행 상황: " + el.textContent);
  done();
})();
