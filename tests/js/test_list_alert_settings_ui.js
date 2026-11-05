// 설정 > 다운로드 리포트 — "새 에피소드 발송 시간"(신규 웹툰 + 새 에피소드를 하루 한 번 보내는 시각).
// ① 디스코드가 설정돼 있고 발송하지 않는 시간이 없을 때만 시각 입력칸이 나온다(비우면 기본 오전 7시) ② 발송하지 않는 시간이 있으면 입력칸은 숨고, "발송하지 않는 시간이 끝난 뒤 첫 리포트" 안내가 나온다
// ③ 시각을 먼저 정해 둔 뒤 발송하지 않는 시간을 처음 설정하면 안내하고, 기존 발송 시간을 유지할지 묻는다(유지하면 그 시각이 계속 쓰이고, 아니면 해제하고 발송하지 않는 시간 기준으로) ④ 유지 중인 시각은 "발송 시간 해제"로 지울 수 있다
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const server = { time: null, discord: true, quiet: [], posts: [] };
  const state = () => ({ time: server.time, default_time: "07:00", discord_configured: server.discord, quiet_hours_set: server.quiet.length > 0, mode: server.time || !server.quiet.length ? "time" : "quiet" });
  const { w, run, calls } = makeApp({
    "/api/settings/report": (opts) => { if (opts && opts.method === "POST") { const body = JSON.parse(opts.body); server.quiet = body.quiet_hours; server.posts.push(["report", body]); } return { quiet_hours: server.quiet, batch_minutes: 10 }; },
    "/api/settings/list-alert": (opts) => { if (opts && opts.method === "POST") { const body = JSON.parse(opts.body); server.time = body.time; server.posts.push(["list-alert", body]); } return state(); },
    "/api/settings/viewer-scan": { enabled: false, max_wait_minutes: 10, token_set: false },
  });
  await idle();
  const $ = (id) => w.document.getElementById(id);
  const shown = (id) => !$(id).classList.contains("hidden");
  const load = async () => { run("loadReportSettings();"); await idle(); };
  const QUIET = { start_hour: 23, start_minute: 0, end_hour: 6, end_minute: 0, days: [] };
  let confirms = [], answer = true; w.confirm = (message) => { confirms.push(message); return answer; };

  // Scenario 1: 디스코드 설정 + 발송하지 않는 시간 없음 → 입력칸이 나오고(정한 시각 없으면 비어 있고 기본값 안내), 저장하면 그 시각이 서버로 간다. 비우고 저장하면 지운다
  await load(); ok(shown("list-alert-time-edit") && !shown("list-alert-kept") && !shown("list-alert-quiet-hint") && $("list-alert-time").value === "", "처음: 입력칸만");
  ok($("list-alert-time-edit").textContent.includes("오전 7시") && $("list-alert-time-edit").textContent.includes("발송하지 않는 시간"), "기본 시각/발송하지 않는 시간 안내");
  $("list-alert-time").value = "18:30"; $("btn-save-list-alert").click(); await idle();
  ok(JSON.stringify(server.posts.at(-1)) === JSON.stringify(["list-alert", { time: "18:30" }]) && server.time === "18:30" && $("list-alert-save-result").textContent === "저장했습니다.", "시각 저장: " + JSON.stringify(server.posts.at(-1)));
  await load(); ok($("list-alert-time").value === "18:30" && !shown("list-alert-kept") && !shown("list-alert-quiet-hint"), "다시 열면 저장한 시각(발송하지 않는 시간이 없으면 \"유지 중\" 줄/안내는 없다)");
  $("list-alert-time").value = ""; $("btn-save-list-alert").click(); await idle(); ok(server.time === null && JSON.stringify(server.posts.at(-1)[1]) === JSON.stringify({ time: null }), "비우고 저장하면 지운다");

  // Scenario 2: 디스코드가 설정돼 있지 않으면 이 설정은 안 나온다
  server.discord = false; await load(); ok(!shown("list-alert-group"), "디스코드 미설정: 그룹이 숨는다"); server.discord = true;

  // Scenario 3: 발송하지 않는 시간이 있고 정한 시각은 없다 → 입력칸 대신 안내(끝난 뒤 첫 리포트, 30분 규칙)
  server.quiet = [QUIET]; await load();
  ok(!shown("list-alert-time-edit") && !shown("list-alert-kept") && shown("list-alert-quiet-hint") && $("list-alert-quiet-hint").textContent.includes("처음 나가는") && $("list-alert-quiet-hint").textContent.includes("30분"), "발송하지 않는 시간이 있으면 안내만: " + $("list-alert-quiet-hint").textContent.slice(0, 60));

  // Scenario 4: 발송하지 않는 시간이 있는데 시각도 유지 중 → "유지 중" 줄과 해제 버튼(누르면 시각을 지우고 발송하지 않는 시간 기준으로 돌아간다)
  server.time = "18:30"; await load();
  ok(shown("list-alert-kept") && $("list-alert-kept-text").textContent.includes("18:30") && !shown("list-alert-time-edit") && !shown("list-alert-quiet-hint"), "유지 중 표시");
  $("btn-clear-list-alert").click(); await idle(); ok(server.time === null && !shown("list-alert-kept") && shown("list-alert-quiet-hint"), "해제하면 발송하지 않는 시간 안내로");
  server.quiet = []; server.time = null; server.posts.length = 0;

  // Scenario 5: 시각을 먼저 정한 뒤 발송하지 않는 시간을 처음 설정하면 — 안내하고 묻는다. 유지하면(확인) 시각은 그대로, 유지하지 않으면(취소) 시각을 지운다
  const saveQuiet = async () => { run("addQuietRow(QUIET_DEFAULT);"); $("btn-save-report-settings").click(); await idle(); };
  server.time = "18:30"; await load(); confirms = []; answer = true; await saveQuiet();
  ok(confirms.length === 1 && confirms[0].includes("18:30") && confirms[0].includes("유지"), "안내 문구: " + confirms[0]);
  ok(server.quiet.length === 1 && server.time === "18:30" && !server.posts.some((p) => p[0] === "list-alert"), "유지: 시각 그대로, 시각 저장 요청 없음");
  ok(shown("list-alert-kept"), "유지 중 표시로 바뀐다");
  server.quiet = []; server.time = "18:30"; server.posts.length = 0; await load(); confirms = []; answer = false; await saveQuiet();
  ok(confirms.length === 1 && server.quiet.length === 1 && server.time === null && JSON.stringify(server.posts.at(-1)) === JSON.stringify(["list-alert", { time: null }]), "유지하지 않음: 시각 해제 — " + JSON.stringify(server.posts));
  ok(shown("list-alert-quiet-hint") && !shown("list-alert-kept"), "발송하지 않는 시간 안내로");

  // Scenario 6: 안내가 필요 없는 경우 — 시각을 안 정했거나, 발송하지 않는 시간이 이미 있었거나, 발송하지 않는 시간을 지울 때는 묻지 않는다
  server.quiet = []; server.time = null; await load(); confirms = []; await saveQuiet(); ok(confirms.length === 0, "시각을 안 정했으면 묻지 않는다");
  server.quiet = [QUIET]; server.time = "18:30"; await load(); confirms = []; $("btn-save-report-settings").click(); await idle(); ok(confirms.length === 0 && server.time === "18:30", "이미 발송하지 않는 시간이 있었으면 묻지 않는다(시각 그대로)");
  server.quiet = []; server.time = "18:30"; await load(); confirms = []; $("btn-save-report-settings").click(); await idle(); ok(confirms.length === 0, "발송하지 않는 시간이 없는 채로 저장해도 묻지 않는다");
  done();
})();
