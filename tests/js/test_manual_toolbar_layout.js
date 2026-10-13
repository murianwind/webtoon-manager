// 수동 다운로드 툴바: 처음에는 "전체선택 / 미보유만 선택 / 기록용 전체선택"만 한 줄로 보이고, 회차를 고르면 그 옆에
// "선택해제 / 선택 다운로드 / 받은 것으로 기록 / 받지 않은 것으로 기록"이 같은 줄에 나타난다. 버튼 아래에는 처음 세 버튼의 짧은 안내가 있다(카카오페이지/네이버).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const kep = (n, state, extra = {}) => ({ number: n, subtitle: `${n - 1}화`, state, expire: null, free_at: null, downloaded: false, archived: false, before_start: false, selectable: !["locked", "waitfree_wait", "rental_needed"].includes(state), ...extra });
  const kakaoBase = { series_id: 69500409, title: "도굴왕", folder: "/dl/x", mode: "compare", existing_count: 1, marker: null, to_download_count: 1, locked_count: 1, downloaded_count: 1, archived_count: 0, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", site_total: 4, listed_count: 4, hidden_count: 0, excluded_video_count: 0, tickets: null,
    episodes: [kep(1, "free", { downloaded: true }), kep(2, "free"), kep(3, "owned"), kep(4, "locked")] };
  let kakao = { ...kakaoBase, subscription: "active" };
  let naver = { title_id: "333", title: "네이버 작품", thumbnail_url: "t", subscription: "active", episodes: [
    { episode_no: 1, subtitle: "1화", owned: true, is_locked: false, recorded: false }, { episode_no: 2, subtitle: "2화", owned: false, is_locked: false, recorded: false }, { episode_no: 3, subtitle: "3화", owned: false, is_locked: true, recorded: false }] };
  const routes = { "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/analyze": () => kakao, "/api/manual-download/analyze": () => naver };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const visible = (ids) => ids.filter((id) => !$(id).classList.contains("hidden"));
  run(`switchToTab("manual-download");`);
  const setPlatform = (p) => { $("manual-platform").value = p; $("manual-platform").dispatchEvent(new w.Event("change")); };
  const analyze = async (q) => { $("manual-query").value = q; $("btn-manual-analyze").click(); await sleep(60); };
  const check = (cls, n, on = true) => { const cb = w.document.querySelector(`.${cls}[data-no="${n}"]`); cb.checked = on; cb.dispatchEvent(new w.Event("change", { bubbles: true })); };

  // ── 카카오페이지 ──
  const K = ["btn-kakao-manual-select-all", "btn-kakao-manual-select-missing", "btn-kakao-manual-record-select-all", "btn-kakao-manual-select-none", "btn-kakao-manual-download", "btn-kakao-manual-record-done", "btn-kakao-manual-record-undone"];
  setPlatform("kakao"); await analyze("69500409");
  const bar = $(K[0]).parentElement;
  // Scenario 1: 버튼 일곱 개가 한 줄(한 컨테이너)에 이 순서로 있고, 그 바로 아래에 안내가 있다
  ok(JSON.stringify([...bar.children].map((b) => b.id)) === JSON.stringify(K) && K.every((id) => $(id).parentElement === bar), "한 줄에 순서대로: " + [...bar.children].map((b) => b.id));
  ok(bar.nextElementSibling && bar.nextElementSibling.classList.contains("manual-actions-hint"), "버튼 바로 아래에 안내");
  // Scenario 2: 처음(구독 중, 선택 없음)에는 처음 세 버튼만 보인다
  ok(JSON.stringify(visible(K)) === JSON.stringify(K.slice(0, 3)), "처음에는 3개: " + visible(K));
  // Scenario 3: 회차를 하나라도 고르면 나머지 네 개가 같은 줄에 나타난다(전체 7개), 모두 지우면 다시 숨는다
  check("kakao-manual-ep-checkbox", 2);
  ok(JSON.stringify(visible(K)) === JSON.stringify(K), "선택하면 7개: " + visible(K));
  check("kakao-manual-ep-checkbox", 2, false);
  ok(JSON.stringify(visible(K)) === JSON.stringify(K.slice(0, 3)), "선택을 지우면 다시 3개: " + visible(K));
  // 버튼으로 고른 경우도 같다(체크박스 이벤트 없이 값만 바뀌는 경로)
  $("btn-kakao-manual-select-all").click(); ok(visible(K).includes("btn-kakao-manual-download"), "전체선택 버튼으로 고르면 나타남");
  $("btn-kakao-manual-select-none").click(); ok(!visible(K).includes("btn-kakao-manual-download"), "선택해제 버튼 뒤에는 다시 숨음");
  // Scenario 4: 안내 — 세 버튼의 짧은 설명이 있고, 숨겨진 버튼의 설명은 같이 숨는다
  const hint = (id) => $(id);
  const hintText = bar.nextElementSibling.textContent.replace(/\s+/g, " ");
  ok(/전체선택/.test(hintText) && /미보유만 선택/.test(hintText) && /기록용 전체선택/.test(hintText) && /잠긴 회차/.test(hintText), "안내 내용: " + hintText);
  // Scenario 5: 구독하지 않은 작품 — 기록용 전체선택/기록 버튼은 없고, 선택하면 선택해제/선택 다운로드만 나타난다. 기록용 안내도 숨는다
  kakao = { ...kakaoBase, subscription: null }; await analyze("69500409");
  ok(JSON.stringify(visible(K)) === JSON.stringify(K.slice(0, 2)), "미구독 처음: " + visible(K));
  check("kakao-manual-ep-checkbox", 2);
  ok(JSON.stringify(visible(K)) === JSON.stringify([K[0], K[1], K[3], K[4]]), "미구독 선택 후: " + visible(K));
  ok(!/기록용 전체선택/.test([...bar.nextElementSibling.children].filter((c) => !c.classList.contains("hidden")).map((c) => c.textContent).join(" ")), "미구독: 기록용 안내 숨김");
  kakao = { ...kakaoBase, subscription: "active" };

  // ── 네이버 ──
  const N = ["btn-manual-select-all", "btn-manual-select-missing", "btn-manual-select-none", "btn-manual-download", "btn-manual-record-done", "btn-manual-record-undone"];
  setPlatform("naver"); await analyze("333");
  const nbar = $(N[0]).parentElement;
  ok(JSON.stringify([...nbar.children].map((b) => b.id)) === JSON.stringify(N) && nbar.nextElementSibling.classList.contains("manual-actions-hint"), "네이버: 한 줄에 순서대로 + 아래 안내: " + [...nbar.children].map((b) => b.id));
  ok(JSON.stringify(visible(N)) === JSON.stringify(N.slice(0, 2)), "네이버 처음에는 전체선택/미보유만 선택: " + visible(N));
  check("manual-ep-checkbox", 2);
  ok(JSON.stringify(visible(N)) === JSON.stringify(N), "네이버 선택하면 6개: " + visible(N));
  check("manual-ep-checkbox", 2, false);
  ok(JSON.stringify(visible(N)) === JSON.stringify(N.slice(0, 2)), "네이버 선택을 지우면 다시 2개");
  ok(/전체선택/.test(nbar.nextElementSibling.textContent) && /미보유만 선택/.test(nbar.nextElementSibling.textContent), "네이버 안내: " + nbar.nextElementSibling.textContent.replace(/\s+/g, " "));
  naver = { ...naver, subscription: null }; await analyze("333");
  check("manual-ep-checkbox", 2);
  ok(JSON.stringify(visible(N)) === JSON.stringify([N[0], N[1], N[2], N[3]]), "네이버 미구독 선택 후: " + visible(N));
  done();
})();
