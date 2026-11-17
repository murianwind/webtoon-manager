// 설정 > 다운로드 리포트 — "회차가 늘지 않는 웹툰 알림(일)". 설정 화면을 열면 서버 값이 입력칸에 나오고, 저장하면 서버로 가며, 서버가 거절하면 이유가 보인다. 0은 "끔" 안내가 나온다.
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const server = { days: 30, posts: [], reject: null };
  const { w, run } = makeApp({
    "/api/settings/report": { quiet_hours: [], batch_minutes: 10 },
    "/api/settings/list-alert": { time: null, default_time: "07:00", discord_configured: true, quiet_hours_set: false, mode: "time" },
    "/api/settings/viewer-scan": { enabled: false, max_wait_minutes: 10, token_set: false },
    "/api/settings/stalled-episodes": (opts) => {
      if (opts && opts.method === "POST") {
        const body = JSON.parse(opts.body); server.posts.push(body);
        if (server.reject) { const e = new Error(server.reject); e.status = 400; throw e; }
        server.days = body.days;
      }
      return { days: server.days, default_days: 30, max_days: 365 };
    },
  });
  await idle();
  const $ = (id) => w.document.getElementById(id);
  const load = async () => { run("loadReportSettings();"); await idle(); };

  await load(); ok($("stalled-days").value === "30" && $("stalled-off-hint").classList.contains("hidden"), "처음: 서버 값(30)이 보이고 '끔' 안내는 없다");
  $("stalled-days").value = "45"; $("btn-save-stalled").click(); await idle();
  ok(JSON.stringify(server.posts.at(-1)) === JSON.stringify({ days: 45 }) && server.days === 45 && $("stalled-save-result").textContent === "저장했습니다.", "저장: " + JSON.stringify(server.posts.at(-1)));
  $("stalled-days").value = "0"; $("btn-save-stalled").click(); await idle();
  ok(server.days === 0 && !$("stalled-off-hint").classList.contains("hidden"), "0이면 끔 안내");
  $("stalled-days").value = "abc"; $("btn-save-stalled").click(); await idle();
  ok(server.posts.length === 2 && $("stalled-save-result").textContent.includes("0~365"), "숫자가 아니면 보내지 않고 안내: " + $("stalled-save-result").textContent);
  $("stalled-days").value = "400"; $("btn-save-stalled").click(); await idle();
  ok(server.posts.length === 2 && $("stalled-save-result").textContent.includes("0~365"), "범위 밖은 보내지 않는다");
  server.reject = "서버 거절 사유"; $("stalled-days").value = "20"; $("btn-save-stalled").click(); await idle();
  ok($("stalled-save-result").textContent === "서버 거절 사유", "서버가 거절하면 이유 표시");
  done();
})();
