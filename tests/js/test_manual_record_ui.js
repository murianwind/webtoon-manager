// 수동 다운로드: 구독 중인 작품의 "받은 기록" 고치기 — 선택한 회차를 "받은 것으로" 또는 "받지 않은 것으로" 기록한다(카카오페이지/네이버 공통).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const kep = (n, state, extra = {}) => ({ number: n, subtitle: `${n - 1}화`, state, expire: null, free_at: null, downloaded: false, archived: false, before_start: false, selectable: !["locked", "waitfree_wait", "rental_needed"].includes(state), ...extra });
  const kakaoBase = { series_id: 69500409, title: "도굴왕", folder: "/dl/x", mode: "compare", existing_count: 1, marker: null, to_download_count: 1, locked_count: 2, downloaded_count: 1, archived_count: 0, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", site_total: 5, listed_count: 5, hidden_count: 0, excluded_video_count: 0, tickets: null,
    episodes: [kep(1, "free", { downloaded: true }), kep(2, "free"), kep(3, "owned"), kep(4, "locked"), kep(5, "waitfree_wait")] };
  let kakao = { ...kakaoBase, subscription: "active" };
  let naver = { title_id: "333", title: "네이버 작품", thumbnail_url: "t", subscription: "active", episodes: [
    { episode_no: 1, subtitle: "1화", owned: true, is_locked: false, recorded: false }, { episode_no: 2, subtitle: "2화", owned: true, is_locked: false, recorded: true },
    { episode_no: 3, subtitle: "3화", owned: false, is_locked: false, recorded: false }, { episode_no: 4, subtitle: "4화", owned: false, is_locked: true, recorded: false }] };
  const posts = [], analyzeCalls = { kakao: 0, naver: 0 };
  let kakaoRecordResponse = { marked: [2, 3], ignored_in_folder: [], recorded_count: 3, pending_count: 0 };
  let naverRecordResponse = { previous: 2, last_downloaded_no: 3, marked: [3], ignored_locked: [], folder_last_no: 1, warning: null };
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/analyze": () => { analyzeCalls.kakao++; return kakao; }, "/api/manual-download/analyze": () => { analyzeCalls.naver++; return naver; },
    "/api/kakao-manual/record": (opts) => { posts.push(["kakao", JSON.parse(opts.body)]); return kakaoRecordResponse; },
    "/api/manual-download/record": (opts) => { posts.push(["naver", JSON.parse(opts.body)]); return naverRecordResponse; },
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const confirms = [], alerts = []; let answer = true;
  w.confirm = (m) => { confirms.push(m); return answer; }; w.alert = (m) => alerts.push(m);
  run(`switchToTab("manual-download");`);
  const setPlatform = (p) => { $("manual-platform").value = p; $("manual-platform").dispatchEvent(new w.Event("change")); };
  const analyze = async (q) => { $("manual-query").value = q; $("btn-manual-analyze").click(); await sleep(60); };
  const box = (cls, n) => w.document.querySelector(`.${cls}[data-no="${n}"]`);

  // ── 카카오페이지 ──
  setPlatform("kakao"); await analyze("69500409");
  // Scenario 1: 구독 중이면 기록 영역이 보이고, 구독하지 않으면(구독해제/미구독) 숨는다
  ok(!$("kakao-manual-record-actions").classList.contains("hidden"), "구독 중: 기록 영역이 보인다");
  kakao = { ...kakaoBase, subscription: null }; await analyze("69500409");
  ok($("kakao-manual-record-actions").classList.contains("hidden"), "구독 안 함: 기록 영역이 숨는다");
  ok(box("kakao-manual-ep-checkbox", 4).disabled && box("kakao-manual-ep-checkbox", 5).disabled, "구독 안 함: 잠긴 회차는 예전처럼 선택 불가");
  kakao = { ...kakaoBase, subscription: "active" }; await analyze("69500409");

  // Scenario 2: 구독 중이면 잠긴 회차도 체크할 수 있다(기록용). 다만 "전체선택"/"미보유만 선택"은 받을 수 있는 회차만 고른다
  ok(!box("kakao-manual-ep-checkbox", 4).disabled && box("kakao-manual-ep-checkbox", 4).dataset.selectable === "0" && box("kakao-manual-ep-checkbox", 2).dataset.selectable === "1", "잠긴 회차 체크 가능 + 선택 가능 표시");
  $("btn-kakao-manual-select-all").click();
  ok(box("kakao-manual-ep-checkbox", 2).checked && !box("kakao-manual-ep-checkbox", 4).checked && !box("kakao-manual-ep-checkbox", 5).checked, "전체선택은 받을 수 있는 회차만");
  $("btn-kakao-manual-select-none").click();
  ok([1, 2, 3, 4, 5].every((n) => !box("kakao-manual-ep-checkbox", n).checked), "선택해제는 전부 해제");
  $("btn-kakao-manual-record-select-all").click();
  ok([1, 2, 3, 4, 5].every((n) => box("kakao-manual-ep-checkbox", n).checked), "기록용 전체선택은 잠긴 회차까지 전부");
  $("btn-kakao-manual-select-none").click();

  // Scenario 3: 선택이 없으면 안내만(요청 없음)
  $("btn-kakao-manual-record-done").click(); await sleep(30);
  ok(posts.length === 0 && alerts.some((m) => /기록할 회차를 선택/.test(m)), "선택 없음 안내: " + alerts.join("|"));
  alerts.length = 0;

  // Scenario 4: 받은 것으로 기록 — 확인 창(개수 + 폴더 파일은 안 건드림)을 거쳐 요청하고, 결과 안내 뒤 분석을 다시 읽는다. 취소하면 요청 없음
  box("kakao-manual-ep-checkbox", 2).checked = true; box("kakao-manual-ep-checkbox", 4).checked = true;       // 4번은 잠긴 회차
  answer = false; $("btn-kakao-manual-record-done").click(); await sleep(30);
  ok(posts.length === 0 && confirms.length === 1 && /2개/.test(confirms[0]) && /받은 것으로/.test(confirms[0]) && /폴더의 파일은 건드리지 않/.test(confirms[0]), "확인 창: " + confirms[0]);
  answer = true; const before = analyzeCalls.kakao; $("btn-kakao-manual-record-done").click(); await sleep(80);
  ok(posts.length === 1 && posts[0][0] === "kakao" && JSON.stringify(posts[0][1]) === JSON.stringify({ series_id: 69500409, numbers: [2, 4], downloaded: true }), "받은 것으로 요청: " + JSON.stringify(posts[0]));
  ok(alerts.some((m) => /2개/.test(m) && /기록했습니다/.test(m)) && analyzeCalls.kakao === before + 1, "결과 안내 + 분석 다시 읽기: " + alerts.join("|") + " / " + (analyzeCalls.kakao - before));
  alerts.length = 0;

  // Scenario 5: 받지 않은 것으로 기록 — 자동으로 받는 대상이 됨을 알리고, 폴더에 파일이 있어 못 바꾼 회차를 결과에 알려 준다
  kakaoRecordResponse = { marked: [3], ignored_in_folder: [1], recorded_count: 1, pending_count: 1 };
  box("kakao-manual-ep-checkbox", 1).checked = true; box("kakao-manual-ep-checkbox", 3).checked = true;
  $("btn-kakao-manual-record-undone").click(); await sleep(80);
  ok(/받지 않은 것으로/.test(confirms[confirms.length - 1]) && /자동으로 받는 대상/.test(confirms[confirms.length - 1]) && /폴더에 파일이 있는 회차는 바꿀 수 없/.test(confirms[confirms.length - 1]), "확인 창: " + confirms[confirms.length - 1]);
  ok(posts.length === 2 && posts[1][1].downloaded === false && JSON.stringify(posts[1][1].numbers) === JSON.stringify([1, 3]), "받지 않은 것으로 요청: " + JSON.stringify(posts[1]));
  ok(alerts.some((m) => /1개/.test(m) && /1번/.test(m) && /폴더에 파일이 있/.test(m)), "못 바꾼 회차 안내: " + alerts.join("|"));
  alerts.length = 0;

  // Scenario 6: 선택 다운로드에는 받을 수 없는 회차(잠긴 회차)가 섞여 들어가지 않는다
  $("btn-kakao-manual-select-none").click();
  box("kakao-manual-ep-checkbox", 2).checked = true; box("kakao-manual-ep-checkbox", 4).checked = true;
  const runPosts = []; const realFetch = w.fetch;
  w.fetch = async (url, opts = {}) => { if (String(url).includes("/api/kakao-manual/run")) { runPosts.push(JSON.parse(opts.body)); return { ok: true, status: 200, json: async () => ({ status: "started" }) }; } return realFetch(url, opts); };
  $("btn-kakao-manual-download").click(); await sleep(60); w.fetch = realFetch;
  ok(runPosts.length === 1 && JSON.stringify(runPosts[0].numbers) === JSON.stringify([2]), "다운로드 요청에서 잠긴 회차 제외: " + JSON.stringify(runPosts));

  // ── 네이버 ──
  posts.length = 0; confirms.length = 0; alerts.length = 0;
  setPlatform("naver"); await analyze("333");
  ok(!$("manual-record-actions").classList.contains("hidden"), "네이버 구독 중: 기록 영역이 보인다");
  ok(w.document.querySelector('.manual-ep-checkbox[data-no="4"]').disabled, "네이버: 잠긴 회차는 기록할 수 없어 선택 불가");
  ok(w.document.querySelector("#manual-tbody tr:nth-child(2)").textContent.includes("기록"), "기록으로만 받은 회차는 구분해서 보인다: " + w.document.querySelector("#manual-tbody tr:nth-child(2)").textContent.replace(/\s+/g, " "));
  // 받은 것으로
  w.document.querySelector('.manual-ep-checkbox[data-no="3"]').checked = true;
  const nBefore = analyzeCalls.naver; $("btn-manual-record-done").click(); await sleep(80);
  ok(/3화/.test(confirms[0]) && /여기까지 받음/.test(confirms[0]) && /받은 것으로/.test(confirms[0]), "네이버 확인 창: " + confirms[0]);
  ok(posts.length === 1 && JSON.stringify(posts[0][1]) === JSON.stringify({ title_id: "333", episode_nos: [3], downloaded: true }), "네이버 받은 것으로 요청: " + JSON.stringify(posts[0]));
  ok(alerts.some((m) => /2화/.test(m) && /3화/.test(m)) && analyzeCalls.naver === nBefore + 1, "네이버 결과(이전→새 기록) + 분석 다시: " + alerts.join("|"));
  alerts.length = 0;
  // 받지 않은 것으로 + 폴더 경고
  naverRecordResponse = { previous: 3, last_downloaded_no: 0, marked: [1], ignored_locked: [], folder_last_no: 2, warning: "폴더에 2화까지 파일이 있어서 다음 실행에서 다시 2화까지 받은 것으로 인식됩니다(파일을 지우면 반영됩니다)." };
  w.document.querySelectorAll(".manual-ep-checkbox").forEach((cb) => (cb.checked = false)); w.document.querySelector('.manual-ep-checkbox[data-no="1"]').checked = true;
  $("btn-manual-record-undone").click(); await sleep(80);
  ok(/1화부터/.test(confirms[1]) && /받을 대상/.test(confirms[1]), "네이버 확인 창(받지 않은 것으로): " + confirms[1]);
  ok(posts[1][1].downloaded === false && alerts.some((m) => /다시 2화까지/.test(m)), "네이버 경고 표시: " + alerts.join("|"));
  // 구독 안 함이면 기록 영역이 숨는다
  naver = { ...naver, subscription: null }; await analyze("333");
  ok($("manual-record-actions").classList.contains("hidden"), "네이버 구독 안 함: 숨는다");
  done();
})();
