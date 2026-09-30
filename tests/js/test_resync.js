const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let status = { status: "running", log: [] }; let kakaoAuthorCalls = 0;
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/jobs/status": () => ({ registry: status }), "/api/registry/resync": { status: "started" },
    "/api/authors/interested": [], "/api/authors/candidates": [], "/api/tags/interested": [], "/api/watched-tags": [], "/api/tags/catalog": [],
    "/api/kakao/watched-authors": () => { kakaoAuthorCalls++; return []; }, "/api/kakao/authors/candidates": [],
  };
  const { w, run, calls } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  ok($("page-registry").textContent.includes("카카오웹툰 관리를 켰으면 카카오페이지 작품도 함께 처리"), "안내 문구에 카카오 처리 설명이 있음");
  $("btn-resync-registry").click(); await sleep(30);
  ok(calls.some((c) => c.url === "/api/registry/resync" && c.method === "POST"), "재동기화 요청을 보냄");
  const before = kakaoAuthorCalls; status = { status: "success", log: [] };
  run("window.__p = refreshRegistryJobStatuses();"); await w.__p; await sleep(30);
  ok(kakaoAuthorCalls > before, "끝나면 카카오 작가 목록도 새로 불러옴");
  const b2 = kakaoAuthorCalls; run("kakaoWebtoonsEnabled = false;"); run("window.__p = refreshRegistryJobStatuses();"); await w.__p; await sleep(30);
  ok(kakaoAuthorCalls === b2, "카카오웹툰 관리를 꺼 두면 카카오 목록은 부르지 않음");
  done();
})();
