// 수동 다운로드 분석 결과의 "표지·정보 받기" 버튼 — cover와 info.xml이 담긴 zip을 브라우저 다운로드로 받는다(카카오페이지/네이버).
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const kakao = { series_id: 68546405, title: "블루엔젤", folder: "/dl/x", mode: "compare", existing_count: 1, marker: null, to_download_count: 1, locked_count: 0, downloaded_count: 1, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: "active", site_total: 1, listed_count: 1, hidden_count: 0, excluded_video_count: 0, tickets: null,
    episodes: [{ number: 1, subtitle: "1화", state: "free", expire: null, free_at: null, downloaded: false, before_start: false, selectable: true }] };
  const naver = { title_id: "333", title: "네이버 작품", thumbnail_url: "t", subscription: null, episodes: [{ episode_no: 1, subtitle: "1화", owned: false, is_locked: false }] };
  const routes = { "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/analyze": kakao, "/api/manual-download/analyze": naver };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);

  // 브라우저 다운로드를 가로챈다: fetch(zip) → blob → 임시 주소 → 앵커 클릭
  const fetched = [], downloads = [], revoked = []; let respond;
  const realFetch = w.fetch;
  w.fetch = async (url, opts) => { if (String(url).includes("metadata-zip")) { fetched.push(String(url)); return respond(String(url)); } return realFetch(url, opts); };
  w.URL.createObjectURL = () => "blob:test"; w.URL.revokeObjectURL = (u) => revoked.push(u);
  w.HTMLAnchorElement.prototype.click = function () { downloads.push({ href: this.href, download: this.download }); };
  const alerts = []; w.alert = (m) => alerts.push(m);
  const zipResponse = (headers = {}) => ({ ok: true, status: 200, headers: { get: (k) => headers[k.toLowerCase()] ?? null }, blob: async () => new w.Blob(["zip"]) });
  const NAME = "attachment; filename=\"metadata.zip\"; filename*=UTF-8''" + encodeURIComponent("작품_표지정보.zip");

  run(`switchToTab("manual-download");`);
  const setPlatform = (p) => { $("manual-platform").value = p; $("manual-platform").dispatchEvent(new w.Event("change")); };
  const analyze = async (q) => { $("manual-query").value = q; $("btn-manual-analyze").click(); await sleep(50); };

  // ── 카카오페이지 ──
  setPlatform("kakao"); await analyze("68546405");
  const kbtn = $("btn-kakao-manual-metadata");
  // Scenario 1: 버튼이 "다시 분석" 바로 오른쪽에 있다
  ok(!!kbtn && $("btn-kakao-manual-reanalyze").nextElementSibling === kbtn && kbtn.textContent === "표지·정보 받기", "카카오: '다시 분석' 바로 옆에 '표지·정보 받기'");

  // Scenario 2: 누르면 zip을 받아 브라우저 다운로드를 시작한다 — 한글 파일 이름 그대로, 받는 동안은 눌리지 않는다
  respond = () => zipResponse({ "content-disposition": NAME });
  kbtn.click(); ok(kbtn.disabled === true && kbtn.textContent === "받는 중…", "누르는 즉시 '받는 중…'으로 바뀌고 비활성");
  kbtn.click();       // 연타 무시
  await sleep(50);
  ok(fetched.length === 1 && fetched[0] === "/api/kakao-manual/metadata-zip?series_id=68546405", "요청은 한 번: " + fetched.join(","));
  ok(downloads.length === 1 && downloads[0].download === "작품_표지정보.zip" && downloads[0].href.startsWith("blob:"), "다운로드 시작: " + JSON.stringify(downloads));
  await sleep(150);
  ok(revoked.length === 1 && alerts.length === 0, "임시 주소를 정리하고(바로가 아니라 잠시 뒤) 안내 창은 없다");
  ok(kbtn.disabled === false && kbtn.textContent === "표지·정보 받기", "끝나면 다시 눌러지고 글자가 돌아온다");

  // Scenario 3: 표지를 못 받아 info.xml만 들어 있으면 받기는 하되 안내한다
  respond = () => zipResponse({ "content-disposition": NAME, "x-metadata-warning": "cover-missing" });
  kbtn.click(); await sleep(50);
  ok(downloads.length === 2 && alerts.some((m) => /표지/.test(m) && /info\.xml/.test(m)), "표지 없음 안내: " + alerts.join(" | "));

  // Scenario 4: 실패하면 안내 창, 다운로드는 없고 버튼은 다시 눌러진다
  alerts.length = 0;
  respond = () => ({ ok: false, status: 404, headers: { get: () => null }, json: async () => ({ detail: "작품 정보를 가져오지 못했습니다" }) });
  kbtn.click(); await sleep(50);
  ok(downloads.length === 2 && alerts.some((m) => /작품 정보를 가져오지 못했습니다/.test(m)) && kbtn.disabled === false, "실패 안내 + 다운로드 없음: " + alerts.join(" | "));

  // ── 네이버 ──
  setPlatform("naver"); await analyze("333");
  const nbtn = $("btn-manual-metadata");
  ok(!!nbtn && $("btn-manual-reanalyze").nextElementSibling === nbtn && nbtn.textContent === "표지·정보 받기", "네이버: '다시 분석' 바로 옆에 '표지·정보 받기'");
  fetched.length = 0; alerts.length = 0;
  respond = () => zipResponse({ "content-disposition": NAME });
  nbtn.click(); await sleep(50);
  ok(fetched.length === 1 && fetched[0] === "/api/manual-download/metadata-zip?title_id=333" && downloads.length === 3 && downloads[2].download === "작품_표지정보.zip", "네이버 받기: " + fetched.join(",") + " / " + JSON.stringify(downloads[2]));
  ok(nbtn.disabled === false && alerts.length === 0, "끝나면 다시 눌러진다");
  done();
})();
