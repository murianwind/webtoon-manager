// 설정 화면의 "다운로드 속도 조절" — 작품당 한 번에 받을 회차와 쉬는 시간을 보고 바꾸고(재시작 없이 적용), 기본값으로 되돌린다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let state = { max_new_episodes_per_title: 10, batch_rest_minutes: 5, default_max_new_episodes_per_title: 10, default_batch_rest_minutes: 5, customized: false };
  const calls = [];
  const routes = { "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings/download-pacing": (opts) => {
      calls.push([(opts && opts.method) || "GET", opts && opts.body ? JSON.parse(opts.body) : null]);
      if (opts && opts.method === "POST") { const b = JSON.parse(opts.body); state = { ...state, ...b, customized: true }; }
      if (opts && opts.method === "DELETE") state = { ...state, max_new_episodes_per_title: 10, batch_rest_minutes: 5, customized: false };
      return state;
    } };
  const { w, run } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("settings");`); await sleep(60);

  // Scenario 1: 설정을 열면 지금 쓰는 값과 기본값 안내가 나온다
  ok($("pacing-cap").value === "10" && $("pacing-rest").value === "5", "지금 값이 입력칸에: " + $("pacing-cap").value + "/" + $("pacing-rest").value);
  ok(/기본값/.test($("settings-card-pacing").textContent) && /10/.test($("pacing-default-note").textContent) && /5/.test($("pacing-default-note").textContent), "기본값 안내: " + $("pacing-default-note").textContent);
  ok($("btn-reset-pacing").disabled === true, "바꾼 값이 없으면 '기본값으로'는 비활성");

  // Scenario 2: 입력을 바꿔 저장하면 서버로 가고 저장 문구가 나온다
  $("pacing-cap").value = "20"; $("pacing-rest").value = "1"; $("btn-save-pacing").click(); await sleep(40);
  const post = calls.filter((c) => c[0] === "POST");
  ok(post.length === 1 && post[0][1].max_new_episodes_per_title === 20 && post[0][1].batch_rest_minutes === 1, "저장 요청: " + JSON.stringify(post));
  ok(/저장했습니다/.test($("pacing-save-result").textContent) && $("btn-reset-pacing").disabled === false, "저장 문구 + '기본값으로' 활성");

  // Scenario 3: 범위 밖/빈 값은 서버로 보내기 전에 막고 이유를 보여 준다
  const before = calls.length;
  $("pacing-rest").value = "0.1"; $("btn-save-pacing").click(); await sleep(20);
  ok(calls.length === before && /30초|0\.5/.test($("pacing-save-result").textContent), "쉬는 시간이 너무 짧으면 요청 없이 안내: " + $("pacing-save-result").textContent);
  $("pacing-rest").value = "1"; $("pacing-cap").value = ""; $("btn-save-pacing").click(); await sleep(20);
  ok(calls.length === before && /회차/.test($("pacing-save-result").textContent), "빈 값이면 요청 없이 안내");
  $("pacing-cap").value = "-3"; $("btn-save-pacing").click(); await sleep(20);
  ok(calls.length === before, "음수는 요청 없이 막는다");

  // Scenario 4: 서버가 거절하면(400) 그 이유를 보여 준다
  const realFetch = w.fetch;
  w.fetch = async (url, opts = {}) => String(url).includes("download-pacing") && opts.method === "POST" ? { ok: false, status: 400, json: async () => ({ detail: "쉬는 시간은 0.5~120분 사이여야 합니다." }) } : realFetch(url, opts);
  $("pacing-cap").value = "10"; $("pacing-rest").value = "5"; $("btn-save-pacing").click(); await sleep(40);
  ok(/0\.5~120분/.test($("pacing-save-result").textContent), "서버 거절 이유: " + $("pacing-save-result").textContent);
  w.fetch = realFetch;

  // Scenario 5: 기본값으로 되돌리면 DELETE 요청, 입력칸이 기본값으로 돌아오고 버튼이 다시 비활성
  $("btn-reset-pacing").click(); await sleep(40);
  ok(calls.some((c) => c[0] === "DELETE") && $("pacing-cap").value === "10" && $("pacing-rest").value === "5" && $("btn-reset-pacing").disabled === true, "기본값으로: " + $("pacing-cap").value + "/" + $("pacing-rest").value);
  done();
})();
