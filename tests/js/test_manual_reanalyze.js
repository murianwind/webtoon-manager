// 수동 다운로드 분석 결과 제목 옆 "다시 분석" 버튼 — 지금 보고 있는 작품을 같은 방법으로 다시 분석한다(카카오페이지/네이버).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let kakao = { series_id: 68546405, title: "블루엔젤", folder: "/dl/x", mode: "compare", existing_count: 1, marker: null, to_download_count: 1, locked_count: 0, downloaded_count: 1, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: "active", site_total: 2, listed_count: 2, hidden_count: 0, excluded_video_count: 0, tickets: null,
    episodes: [{ number: 1, subtitle: "1화", state: "free", expire: null, free_at: null, downloaded: true, before_start: false, selectable: true }, { number: 2, subtitle: "2화", state: "free", expire: null, free_at: null, downloaded: false, before_start: false, selectable: true }] };
  let naver = { title_id: "333", title: "네이버 작품", thumbnail_url: "t", subscription: null, episodes: [{ episode_no: 1, subtitle: "1화", owned: false, is_locked: false }] };
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/analyze": () => kakao, "/api/manual-download/analyze": () => naver,
  };
  const { w, run, calls } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const analyzeCalls = (path) => calls.filter((c) => c.url.startsWith(path)).length;
  const setPlatform = (p) => { $("manual-platform").value = p; $("manual-platform").dispatchEvent(new w.Event("change")); };
  run(`switchToTab("manual-download");`);

  // ── 카카오페이지 ──
  setPlatform("kakao"); $("manual-query").value = "68546405"; $("btn-manual-analyze").click(); await sleep(50);
  const kbtn = $("btn-kakao-manual-reanalyze");
  // Scenario 1: 버튼이 구독 버튼, 작품 페이지 버튼 바로 오른쪽에 있고, 분석 결과 안에 있어서 분석 전에는 보이지 않는다
  ok(!!kbtn && $("link-kakao-manual-page").nextElementSibling === kbtn && kbtn.textContent === "다시 분석", "카카오: 작품 페이지 버튼 바로 옆에 '다시 분석'");
  ok($("kakao-manual-result").contains(kbtn) && !$("kakao-manual-result").classList.contains("hidden"), "카카오: 분석 결과 영역 안에 있고 분석 후에 보인다");

  // Scenario 2: 누르면 같은 작품을 다시 분석하고, 표가 새 결과로 바뀐다. 누르는 동안은 눌리지 않는다
  const before = analyzeCalls("/api/kakao-manual/analyze");
  kakao = { ...kakao, downloaded_count: 2, episodes: kakao.episodes.map((e) => ({ ...e, downloaded: true })), title: "블루엔젤 (갱신)" };
  kbtn.click();
  ok(kbtn.disabled === true && kbtn.textContent === "분석 중…", "카카오: 누르는 즉시 '분석 중…'으로 바뀌고 비활성");
  kbtn.click();   // 연타해도 요청이 겹치지 않는다
  await sleep(50);
  ok(analyzeCalls("/api/kakao-manual/analyze") === before + 1, "카카오: 요청은 한 번만 나간다(연타 무시): " + (analyzeCalls("/api/kakao-manual/analyze") - before));
  const lastCall = calls.filter((c) => c.url.startsWith("/api/kakao-manual/analyze")).pop();
  ok(lastCall.url.includes("series_id=68546405"), "카카오: 같은 작품 번호로 분석: " + lastCall.url);
  ok($("kakao-manual-title").textContent === "블루엔젤 (갱신)" && w.document.querySelectorAll("#kakao-manual-tbody .kp-done").length === 2, "카카오: 제목과 표가 새 결과로 바뀜");
  ok(kbtn.disabled === false && kbtn.textContent === "다시 분석", "카카오: 끝나면 다시 눌러지고 글자가 돌아온다");

  // Scenario 3: 분석에 실패하면 알림 창이 뜨고, 이전 표는 그대로이며, 버튼은 다시 눌러진다
  const realFetch = w.fetch;
  w.fetch = async () => ({ ok: false, status: 502, json: async () => ({ detail: "회차 목록을 가져오지 못했습니다" }) });
  kbtn.click(); await sleep(50);
  ok(w.__alerts.some((m) => /가져오지 못했습니다/.test(m)), "카카오: 실패 안내 창");
  ok(kbtn.disabled === false && kbtn.textContent === "다시 분석" && $("kakao-manual-title").textContent === "블루엔젤 (갱신)", "카카오: 실패해도 버튼이 다시 눌러지고 이전 표가 남는다");
  w.fetch = realFetch;

  // ── 네이버 ──
  setPlatform("naver"); $("manual-query").value = "333"; $("btn-manual-analyze").click(); await sleep(50);
  const nbtn = $("btn-manual-reanalyze");
  ok(!!nbtn && $("link-manual-page").nextElementSibling === nbtn && nbtn.textContent === "다시 분석", "네이버: 작품 페이지 버튼 바로 옆에 '다시 분석'");
  ok($("manual-result").contains(nbtn) && !$("manual-result").classList.contains("hidden"), "네이버: 분석 결과 영역 안에 있고 분석 후에 보인다");
  const nBefore = analyzeCalls("/api/manual-download/analyze");
  naver = { ...naver, title: "네이버 작품 (갱신)", episodes: [...naver.episodes, { episode_no: 2, subtitle: "2화", owned: false, is_locked: false }] };
  nbtn.click();
  ok(nbtn.disabled === true && nbtn.textContent === "분석 중…", "네이버: 누르는 즉시 '분석 중…'으로 바뀌고 비활성");
  await sleep(50);
  ok(analyzeCalls("/api/manual-download/analyze") === nBefore + 1 && calls.filter((c) => c.url.startsWith("/api/manual-download/analyze")).pop().url.includes("title_id=333"), "네이버: 같은 titleId로 한 번 분석");
  ok($("manual-title-name").textContent === "네이버 작품 (갱신)" && w.document.querySelectorAll("#manual-tbody tr").length === 2, "네이버: 제목과 표가 새 결과로 바뀜");
  ok(nbtn.disabled === false && nbtn.textContent === "다시 분석", "네이버: 끝나면 다시 눌러진다");
  done();
})();
