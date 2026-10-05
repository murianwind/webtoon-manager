// 카카오 수동 다운로드 분석 — 아카이빙으로 보관 폴더에 옮겨진 회차(받은 회차 기록에는 있고 폴더에는 없음)를 "이미받음"으로 보여 주되 구분해서 표시
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const row = (n, extra = {}) => ({ number: n, subtitle: `${n}화`, state: "free", expire: null, free_at: null, downloaded: false, archived: false, before_start: false, selectable: true, ...extra });
  const episodes = [
    row(1, { before_start: true }),                                   // 기록 앞 → 이전 회차
    row(2, { downloaded: true, archived: true }), row(3, { downloaded: true, archived: true }),     // 보관 폴더로 옮겨진 회차
    row(4, { downloaded: true }),                                     // 폴더에 있음
    row(5, { state: "waitfree", selectable: true }),                  // 빈 회차(기다무 대기)
    row(6),                                                           // 받을 회차
  ];
  const base = { series_id: 1, title: "분석작", folder: "/dl/분석작", mode: "compare", existing_count: 1, marker: null, to_download_count: 1, locked_count: 1, downloaded_count: 3, archived_count: 2,
    before_start_count: 1, cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: null, tickets: null, site_total: 6, listed_count: 6, excluded_video_count: 0, episodes };
  let cur = base;
  const routes = { "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/kakao-manual/analyze": () => cur };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("manual-download");`); $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  const analyze = async (a) => { cur = a; $("manual-query").value = "1"; $("btn-manual-analyze").click(); await sleep(40); return $("kakao-manual-summary").textContent; };
  const cell = (r) => w.document.querySelectorAll("#kakao-manual-tbody tr")[r].children[5].textContent.replace(/\s+/g, " ").trim();

  let t = await analyze(base);
  ok(t.includes("이미 받음 3개(그중 보관 폴더로 옮긴 2개)"), "요약: 이미 받음 3개(그중 보관 폴더로 옮긴 2개): " + t.slice(-120));
  ok(cell(1) === "이미받음 보관 폴더로 옮김" && cell(2) === "이미받음 보관 폴더로 옮김", "보관된 회차는 '이미받음 + 보관 폴더로 옮김': " + cell(1));
  ok(cell(3) === "이미받음", "폴더에 있는 회차는 그냥 '이미받음': " + cell(3));
  ok(cell(0) === "이전 회차" && cell(4) === "대기" && cell(5) === "대기", "이전 회차/아직 못 받은 회차 구분: " + [cell(0), cell(4), cell(5)]);
  t = await analyze({ ...base, archived_count: 0, downloaded_count: 1, episodes: [row(1, { downloaded: true }), row(2)] });
  ok(!t.includes("보관 폴더로 옮긴"), "보관된 회차가 없으면 보관 문구를 붙이지 않음");
  done();
})();
