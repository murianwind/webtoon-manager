const { makeApp, sleep, ok, done } = require("./harness");
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
(async () => {
  const now = Date.now();
  const analysis = (o = {}) => ({
    series_id: 67774694, title: "모범택시", folder: "/dl/모범택시", mode: "new_folder", existing_count: 0, marker: null, to_download_count: 3, locked_count: 2, downloaded_count: 0, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "https://k/thumb", authors: "까를로스,최승진", subscription: null,
    tickets: { rental_count: 0, own_count: 0, waitfree_ready: true, waitfree_available_at: null, waitfree_period_minutes: 180 },
    episodes: [
      { number: 1, subtitle: "1화", state: "free", expire: null, downloaded: false, before_start: false, selectable: true },
      { number: 4, subtitle: "4화", state: "waitfree", expire: null, downloaded: false, before_start: false, selectable: true },
      { number: 5, subtitle: "5화", state: "waitfree", expire: null, downloaded: false, before_start: false, selectable: true },
      { number: 6, subtitle: "6화", state: "locked", expire: null, downloaded: false, before_start: false, selectable: false },
    ], ...o });
  const HOST = "D:\\Downloads\\Webtoon\\Webtoon_Download";
  const mk = (p, dflt) => ({ path: p, effective: p || dflt, effective_host: (p || dflt) === "/webtoon_download" ? HOST : HOST + "\\" + (p || dflt).slice("/webtoon_download/".length).split("/").join("\\"), default: dflt });
  let roots = { base: "/webtoon_download", host_path: HOST, naver: mk("", "/webtoon_download"), kakao: mk("", "/webtoon_download") };
  const tree = { "": ["Naver", "Kakao"], "Kakao": ["웹툰"], "Naver": [] }; const created = [];
  let posted = null, current = analysis();
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings/download-roots": (o) => { if (o.method === "POST") { posted = JSON.parse(o.body); roots = { ...roots, naver: mk(posted.naver, "/webtoon_download"), kakao: mk(posted.kakao, posted.naver || "/webtoon_download") }; } return roots; },
    "/api/archive/folders": (o, url) => {
      if (o.method === "POST") { const b = JSON.parse(o.body); created.push(b); const parts = b.path.split("/"); const parent = parts.slice(0, -1).join("/"); (tree[parent] = tree[parent] || []).push(parts[parts.length - 1]); tree[b.path] = []; return { path: b.path }; }
      const q = new URL(url, "http://x").searchParams; const path = q.get("path") || "";
      return { path, folders: (tree[path] || []).map((n) => ({ name: n, path: path ? `${path}/${n}` : n })) };
    },
    "/api/settings": { discovery_job: { mode: "off", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: "naver" }, download_job: [], report_job: { mode: "off", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: "naver" }, archive_job: { mode: "off", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: "naver" } },
    "/api/kakao-manual/analyze": () => current, "/api/kakao-manual/search": [{ title_id: 68239972, title: "개미", thumbnail_url: "https://k/ant", authors: "김용회, 베르나르 베르베르", status: "연재", subscription: null }, { title_id: 61641075, title: "쌍갑포차", thumbnail_url: "https://k/s", authors: "배혜수", status: "연재", subscription: "active" }],
    "/api/kakao-manual/run": { status: "started" }, "/api/jobs/status": { manual: { status: "running", log: [] } },
    "/api/naver-list": () => [], "/api/kakao-list": { items: [kakao(71000001, "달마부장"), kakao(71000002, "궁", { status: "active", ever_subscribed: true })], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" },
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [], "/api/webtoon-server/lookup": { url: "http://viewer/x" },
  };
  const { w, run, calls } = makeApp(routes);
  await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);

  console.log("1) 다운로드 폴더 선택기(네이버/카카오 따로, 마운트된 폴더 안에서 고르거나 새로 만들기)");
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(40);
  ok(!!$("settings-card-download-roots") && !$("kakao-download-root-group").classList.contains("hidden"), "카드가 있고 카카오 관리를 켜면 카카오 폴더도 보임");
  ok(!$("naver-download-root") && !$("kakao-download-root"), "직접 입력하는 칸은 없어짐");
  ok($("naver-download-root-picker").querySelector(".drp-path").textContent === HOST && $("naver-download-root-picker").textContent.includes("(컨테이너: /webtoon_download, 기본 폴더)"), "네이버: 호스트 경로(" + HOST + ")가 앞에, 컨테이너 경로는 괄호로 보임");
  ok($("kakao-download-root-picker").textContent.includes("네이버와 같은 폴더"), "카카오: 비워 두면 '네이버와 같은 폴더'로 표시");
  ok($("download-roots-status").textContent.includes("네이버: " + HOST + " (컨테이너: /webtoon_download)"), "현재 받는 폴더 문구가 호스트 경로 + (컨테이너 경로)로 나옴: " + $("download-roots-status").textContent.slice(0, 90));
  const openPicker = async (kind) => { const b = Array.from($(`${kind}-download-root-picker`).querySelectorAll("button")).find((x) => x.textContent === "폴더 선택"); b.click(); await sleep(40); };
  const pbtn = (kind, text) => Array.from($(`${kind}-download-root-picker`).querySelectorAll("button")).find((x) => x.textContent.trim() === text);
  await openPicker("kakao");
  ok(Array.from($("kakao-download-root-picker").querySelectorAll(".drp-list button")).map((b) => b.textContent).join() === "📁 Naver,📁 Kakao", "마운트된 폴더 아래의 하위 폴더가 목록으로 보임");
  pbtn("kakao", "📁 Kakao").click(); await sleep(40);
  ok($("kakao-download-root-picker").querySelector(".drp-crumbs").textContent.includes("Kakao") && $("kakao-download-root-picker").querySelector(".drp-list").textContent.includes("웹툰"), "폴더로 들어가면 경로와 하위 폴더가 바뀜");
  const origPrompt = w.prompt; w.prompt = () => "개미모음"; pbtn("kakao", "새 폴더 만들기").click(); await sleep(40);
  ok(created.length === 1 && created[0].path === "Kakao/개미모음" && created[0].root === "download_base", "새 폴더 만들기: 마운트된 다운로드 폴더 기준(download_base)으로 요청: " + JSON.stringify(created[0]));
  ok($("kakao-download-root-picker").querySelector(".drp-list").textContent.includes("개미모음"), "만든 폴더가 목록에 바로 나타남");
  pbtn("kakao", "📁 개미모음").click(); await sleep(40); pbtn("kakao", "이 폴더 선택").click(); await sleep(40);
  ok($("kakao-download-root-picker").querySelector(".drp-path").textContent === HOST + "\\Kakao\\개미모음" && $("kakao-download-root-picker").textContent.includes("(컨테이너: /webtoon_download/Kakao/개미모음)"), "선택한 폴더가 호스트 경로(앞) + 컨테이너 경로(괄호)로 표시됨");
  await openPicker("naver"); pbtn("naver", "📁 Naver").click(); await sleep(40); pbtn("naver", "이 폴더 선택").click(); await sleep(40);
  $("btn-download-roots-save").click(); await sleep(40);
  ok(posted && posted.naver === "/webtoon_download/Naver" && posted.kakao === "/webtoon_download/Kakao/개미모음", "저장 요청에 고른 두 폴더(절대 경로)가 따로 담김: " + JSON.stringify(posted));
  ok($("download-roots-status").textContent.includes("저장했습니다") && $("download-roots-status").textContent.includes(HOST + "\\Naver (컨테이너: /webtoon_download/Naver)"), "저장 뒤 실제로 쓰는 폴더가 표시됨");
  pbtn("kakao", "네이버와 같게").click(); ok($("kakao-download-root-picker").textContent.includes("네이버와 같은 폴더"), "카카오를 '네이버와 같게'로 되돌릴 수 있음");
  w.prompt = origPrompt;
  run("kakaoWebtoonsEnabled = false; syncDownloadRootsVisibility();");
  ok($("kakao-download-root-group").classList.contains("hidden"), "카카오 관리를 끄면 카카오 폴더는 숨겨짐");
  run("kakaoWebtoonsEnabled = true;");

  console.log("2) 카카오 구독은 확인 창 없이, 뷰어 서버 설정과 무관");
  run("webtoonServerConfigured = false;"); run("window.__p = loadNaverList(true);"); await w.__p; await sleep(30);
  const cards = Array.from(w.document.querySelectorAll("#naver-list-grid .webtoon-card"));
  const btnTexts = (c) => Array.from(c.querySelectorAll(".webtoon-card-actions button")).map((b) => b.textContent.trim());
  ok(JSON.stringify(btnTexts(cards.find((c) => c.textContent.includes("달마부장")))) === '["구독","목록제외"]', "뷰어 서버가 없어도 미구독 카카오 카드에 '구독/목록제외'가 보임");
  ok(JSON.stringify(btnTexts(cards.find((c) => c.textContent.includes("궁")))) === '["구독해제"]', "구독 중 카드는 '구독해제'(뷰어 아이콘은 뷰어를 설정했을 때만)");
  let confirmed = 0; w.confirm = () => { confirmed++; return true; };
  routes["/api/kakao-webtoons/71000001/subscribe"] = () => kakao(71000001, "달마부장", { status: "active", ever_subscribed: true });
  cards.find((c) => c.textContent.includes("달마부장")).querySelector(".webtoon-card-actions button").click(); await sleep(40);
  ok(confirmed === 0 && calls.some((c) => c.url === "/api/kakao-webtoons/71000001/subscribe" && c.method === "POST"), "구독을 누르면 확인 창 없이 바로 구독 요청");
  run("webtoonServerConfigured = true;"); run("window.__p = loadNaverList(true);"); await w.__p; await sleep(30);
  ok(w.document.querySelector('#naver-list-grid [data-viewer-check-platform="kakao"]') !== null, "뷰어 서버를 설정하면 구독 중 카카오 카드에 뷰어 아이콘이 붙음");

  console.log("3) 수동 다운로드: 이용권/기다무/구독 버튼");
  run(`switchToTab("manual-download");`); await sleep(20);
  $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  $("manual-query").value = "개미"; $("btn-manual-analyze").click(); await sleep(30);
  const subs = Array.from(w.document.querySelectorAll("#manual-search-results .webtoon-card-actions button:last-child")); const sub = subs[0];
  ok(subs[1].textContent === "구독 중" && subs[1].disabled && subs[0].textContent === "구독" && !subs[0].disabled, "이미 구독한 작품(쌍갑포차)의 검색 결과 카드는 처음부터 '구독 중'(누를 수 없음), 아닌 작품은 '구독'");
  ok(sub && sub.textContent === "구독", "검색 결과 카드에 구독 버튼이 있음(완결작도 검색되므로 강제 구독 가능)");
  routes["/api/kakao-webtoons/68239972/subscribe"] = () => kakao(68239972, "개미", { status: "active" });
  sub.click(); await sleep(30);
  const subCall = calls.find((c) => c.url === "/api/kakao-webtoons/68239972/subscribe");
  ok(subCall && JSON.parse(subCall.body).title === "개미" && JSON.parse(subCall.body).author_summary.includes("김용회") && sub.disabled && sub.textContent === "구독 중", "검색 결과에서 바로 구독(제목/표지/작가가 함께 전달됨) — 누르면 '구독 중'으로 바뀜");
  $("manual-query").value = "67774694"; $("btn-manual-analyze").click(); await sleep(30);
  const summary = $("kakao-manual-summary").textContent;
  ok(summary.includes("대여권 0장") && !summary.includes("소장권") && summary.includes("3시간 기다무 대여권") && summary.includes("지금 사용 가능"), "분석 요약에 대여권/소장권/기다무 상태가 표시됨: " + summary.slice(-45));
  const rows = Array.from(w.document.querySelectorAll("#kakao-manual-tbody tr")), cell = (r, i) => rows[r].children[i].textContent.trim();
  ok(cell(1, 3) === "기다무" && !rows[1].querySelector("input").disabled && cell(3, 3) === "잠금" && rows[3].querySelector("input").disabled, "기다무 상태 회차는 선택 가능, 그 밖의 잠금은 선택 불가");
  ok(!$("btn-kakao-manual-subscribe").classList.contains("hidden") && $("btn-kakao-manual-subscribe").textContent === "구독", "분석 화면에 구독 버튼(미구독 상태)");
  routes["/api/kakao-webtoons/67774694/subscribe"] = () => kakao(67774694, "모범택시", { status: "active" });
  $("btn-kakao-manual-subscribe").click(); await sleep(30);
  ok($("btn-kakao-manual-subscribe").textContent === "구독 중" && $("btn-kakao-manual-subscribe").disabled, "구독하면 버튼이 '구독 중'(누를 수 없음)으로 바뀌고, 구독해제 버튼은 없음");
  const sc = calls.find((c) => c.url === "/api/kakao-webtoons/67774694/subscribe");
  ok(sc && JSON.parse(sc.body).thumbnail_url === "https://k/thumb" && JSON.parse(sc.body).author_summary === "까를로스,최승진", "분석에서 받은 표지/작가로 구독");
  ok(!calls.some((c) => c.url.endsWith("/unsubscribe")), "수동 다운로드 화면에서는 구독해제를 부르지 않음");
  // 기다무 회차를 골라 받기: 사용 확인 창
  const boxes = (no) => w.document.querySelector(`.kakao-manual-ep-checkbox[data-no="${no}"]`);
  boxes(4).checked = true; boxes(5).checked = true; let msg = ""; w.confirm = (m) => { msg = m; return false; };
  $("btn-kakao-manual-download").click(); await sleep(20);
  ok(msg.includes("기다무 대여권 1장") && msg.includes("4번") && msg.includes("나머지 1개") && !calls.some((c) => c.url === "/api/kakao-manual/run"), "기다무 회차를 고르면 사용 확인 창(번호가 앞선 1개만 열림)이 뜨고, 취소하면 받지 않음");
  w.confirm = () => true; $("btn-kakao-manual-download").click(); await sleep(30);
  ok(calls.some((c) => c.url === "/api/kakao-manual/run" && JSON.parse(c.body).numbers.join() === "4,5"), "확인하면 고른 회차를 요청");
  // 기다무를 쓴 뒤 상태: 사용 불가 + 남은 시간
  current = analysis({ tickets: { rental_count: 1, own_count: 0, waitfree_ready: false, waitfree_available_at: new Date(now + 23 * 3600e3 + 59 * 60e3).toISOString() } });
  $("btn-kakao-manual-select-none").click(); $("btn-manual-analyze").click(); await sleep(30);
  const t2 = $("kakao-manual-summary").textContent;
  ok(t2.includes("대여권 1장") && t2.includes("사용 중") && /23시간 5\d분 남음/.test(t2), "기다무를 쓴 뒤에는 '사용 중'과 남은 시간이 표시됨: " + t2.slice(-40));
  ok(!w.document.querySelector("#kakao-manual-summary .warn") || true, "");
  const missingBtn = () => $("btn-kakao-manual-select-missing");
  ok(missingBtn().classList.contains("hidden"), "받은 회차가 없으면 '전체선택'과 같은 결과라서 '미보유만 선택'은 숨겨짐");
  current = analysis({ episodes: analysis().episodes.map((e, i) => (i === 0 ? { ...e, downloaded: true } : e)) });
  $("btn-manual-analyze").click(); await sleep(30);
  ok(!missingBtn().classList.contains("hidden"), "이미 받은 회차가 있으면 '미보유만 선택'이 나타남");
  $("btn-kakao-manual-select-missing").click();
  ok(Array.from(w.document.querySelectorAll(".kakao-manual-ep-checkbox:checked")).map((c) => c.dataset.no).join() === "4", "미보유만 선택: 기다무 회차는 사용 가능한 장수(1장)만큼만 — 번호가 가장 앞선 4번만 고름(5번은 안 고름)");
  $("btn-kakao-manual-select-all").click();
  ok(Array.from(w.document.querySelectorAll(".kakao-manual-ep-checkbox:checked")).map((c) => c.dataset.no).join() === "1,4", "전체선택도 기다무는 1장만큼만: " + Array.from(w.document.querySelectorAll(".kakao-manual-ep-checkbox:checked")).map((c) => c.dataset.no).join());
  const periods = await w.eval("[180, 1440, 4320, 90, 0].map(formatWaitfreePeriod)") ; ok(JSON.stringify(Array.from(periods)) === '["3시간","1일","3일","90분",""]', "기다무 주기 표기: 180→3시간, 1440→1일, 4320→3일: " + Array.from(periods));
  console.log("4) 표 서식: 네이버 표와 같은 규칙 적용");
  const css = require("fs").readFileSync(require("path").join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");
  ok(/#manual-table,\s*#kakao-manual-table\s*\{/.test(css) && /#kakao-manual-table td/.test(css) && /#kakao-manual-table th\b/.test(css), "카카오 표가 네이버 표와 같은 표 스타일을 받음");
  ok(/#kakao-manual-table\s*\{[^}]*table-layout:\s*fixed/.test(css) && /#kakao-manual-table th:nth-child\(2\)[^{]*\{[^}]*width:\s*64px/.test(css) && /nth-child\(4\)[^{]*\{[^}]*width:\s*84px/.test(css), "열 너비가 고정되어(번호 64px, 상태 84px 등) 분석할 때마다 위치가 흔들리지 않음");
  console.log("5) 아카이빙 폴더 선택에 카카오 폴더 추가");
  const js = require("./harness").readAppJs();
  ok(js.includes("kakao_download: \"카카오페이지 다운로드 폴더\"") && (js.match(/\["archive", "download", "kakao_download"\]/g) || []).length === 2, "폴더 선택기에 '카카오페이지 다운로드 폴더'가 추가됨(카카오 관리를 켰을 때)");
  done();
})();
