// 아카이빙 수동 실행 카드의 "중단" 버튼 — 실행 중일 때만 보이고, 누르면 확인한 뒤 중단을 요청한다(지금 옮기는 파일은 끝까지 옮긴 뒤 멈춘다). 실행 중이 아니라는 답이 오면 알려 준다.
// 안내 문구: 뷰어 서버가 설정돼 있으면 읽은 회차만 옮기고 읽음 상태를 확인하지 못한 작품은 옮기지 않으며, 완결 처리로 이동은 읽음 상태를 보지 않는다는 것을 화면에 적는다.
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const server = { status: "idle", log: [], cancelAnswer: "requested", posts: [] };
  const { w, run } = makeApp({
    "/api/jobs/status": () => ({ archive: { status: server.status, log: server.log } }),
    "/api/archive/cancel": (opts) => { server.posts.push(opts && opts.method); return { status: server.cancelAnswer }; },
    "/api/archive/targets": [], "/api/webtoons": [],
  });
  await idle();
  const $ = (id) => w.document.getElementById(id);
  const hidden = (id) => $(id).classList.contains("hidden");
  const confirms = []; let answer = true; w.confirm = (message) => { confirms.push(message); return answer; };
  const alerts = []; w.alert = (message) => alerts.push(message);
  const refresh = async () => { run("refreshArchiveJobStatus();"); await idle(); };

  // Scenario 1: 실행 중이 아니면 중단 버튼은 숨어 있다("지금 이동"은 누를 수 있다)
  await refresh(); ok(hidden("btn-cancel-archive") && !$("btn-run-archive-now").disabled, "idle: 중단 버튼 없음");

  // Scenario 2: 실행 중이면 중단 버튼이 보이고 "지금 이동"은 못 누른다. 화면을 다시 열어 이어서 볼 때도 같다
  server.status = "running"; await refresh();
  ok(!hidden("btn-cancel-archive") && !$("btn-cancel-archive").disabled && $("btn-cancel-archive").textContent.trim() === "중단" && $("btn-run-archive-now").disabled, "running: 중단 버튼 보임");
  server.status = "idle"; await refresh(); ok(hidden("btn-cancel-archive"), "끝나면 숨는다");
  server.status = "idle"; await refresh(); ok(hidden("btn-cancel-archive"));                                                       // 버튼이 숨은 상태에서 시작해야 이어서 보기 경로가 직접 보여 주는지 확인된다
  server.status = "running"; run("window.__p = resumeArchiveJobStatusIfRunning();"); await w.__p; ok(!hidden("btn-cancel-archive"), "화면을 다시 열어 이어서 볼 때도 보인다(이어서 보기가 바로 보여 준다 — 뒤이은 주기 확인을 기다리지 않고)"); await idle();

  // Scenario 3: 누르면 확인을 먼저 — 취소하면 요청하지 않는다, 확인하면 요청하고 버튼이 "요청됨" 상태가 된다(눌러도 또 요청되지 않게 비활성)
  answer = false; $("btn-cancel-archive").click(); await idle();
  ok(confirms.length === 1 && confirms[0].includes("끝까지 옮긴 뒤 멈추고") && server.posts.length === 0, "확인창을 취소하면 요청 안 함: " + confirms[0]);
  answer = true; $("btn-cancel-archive").click(); await idle();
  ok(server.posts.length === 1 && server.posts[0] === "POST" && $("btn-cancel-archive").disabled && $("btn-cancel-archive").textContent.includes("중단 요청됨"), "확인하면 요청: " + $("btn-cancel-archive").textContent);
  await refresh(); ok($("btn-cancel-archive").disabled && $("btn-cancel-archive").textContent.includes("중단 요청됨"), "아직 실행 중이면 요청됨 상태 유지");
  server.status = "success"; await refresh(); ok(hidden("btn-cancel-archive"), "끝나면 숨는다");
  server.status = "running"; await refresh(); ok(!$("btn-cancel-archive").disabled && $("btn-cancel-archive").textContent.trim() === "중단", "다음 실행에서는 다시 처음 상태(요청됨 표시가 남지 않는다)");

  // Scenario 4: 실행 중이 아니라는 답(이미 끝남)이면 알려 주고 상태를 새로 읽는다
  server.cancelAnswer = "idle"; server.status = "idle"; alerts.length = 0; $("btn-cancel-archive").click(); await idle(); await idle();
  ok(alerts.length === 1 && alerts[0].includes("실행 중이 아닙니다") && hidden("btn-cancel-archive"), "이미 끝났으면 안내: " + alerts);

  // Scenario 5: 안내 문구 — 뷰어에서 읽은 회차만 옮긴다, 읽음 상태를 확인하지 못하면 옮기지 않는다, 완결 처리로 이동은 읽음 상태를 보지 않는다
  const card = $("btn-run-archive-now").closest(".settings-card").textContent;
  ok(card.includes("읽은 회차만") && card.includes("확인하지 못하면") && card.includes("옮기지 않") && card.includes("완결 처리") && card.includes("읽음 상태를 보지 않"), "안내 문구");
  done();
})();
