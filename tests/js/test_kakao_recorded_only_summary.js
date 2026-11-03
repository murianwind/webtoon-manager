// 카카오 분석 화면 — 폴더는 비어 있지만 받은 회차 기록이 있는 작품(전체를 아카이빙한 뒤 연재를 재개한 경우)의 안내 문구와 표시
const { makeApp, sleep, idle, ok, done } = require("./harness");
(async () => {
  const ep = (n, state, extra = {}) => ({ number: n, subtitle: `${n - 1}화`, state, expire: null, free_at: null, downloaded: false, archived: false, before_start: false, selectable: state !== "locked", ...extra });
  const recordedOnly = { series_id: 69500409, title: "A 웹툰", folder: "/dl/카카오/A 웹툰", mode: "recorded_only", existing_count: 0, marker: null, to_download_count: 1, locked_count: 0, downloaded_count: 3, archived_count: 3, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: "active", tickets: null, site_total: 4, listed_count: 4, hidden_count: 0, excluded_video_count: 0,
    episodes: [1, 2, 3].map((n) => ep(n, "free", { downloaded: true, archived: true })).concat([ep(4, "free")]) };
  const { w, run } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/kakao-manual/analyze": () => recordedOnly });
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("manual-download");`); $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  $("manual-query").value = "69500409"; $("btn-manual-analyze").click(); await idle();
  const text = $("kakao-manual-summary").textContent.replace(/\s+/g, " ");
  // Scenario 1: 폴더가 비어 있어도 기록이 있다는 것과 새 회차만 받는다는 것, 폴더는 새 회차를 받을 때 만든다는 것을 알려 준다("처음부터 받습니다"가 아니다)
  ok(/폴더는 비어 있지만 받은 회차 기록\(3개\)이 있어서, 기록에 없는 회차만 받습니다/.test(text) && /새 회차를 받을 때 폴더를 만듭니다/.test(text) && !/처음\(프롤로그 포함\)부터/.test(text), "기록만 있는 작품 안내: " + text);
  // Scenario 2: 기록으로만 받은 회차는 "이미받음 + 폴더에 없음"으로 보이고, 받을 회차는 새 회차뿐이다
  const rows = [...w.document.querySelectorAll("#kakao-manual-tbody tr")].map((r) => r.textContent.replace(/\s+/g, " "));
  ok(rows.slice(0, 3).every((r) => /이미받음/.test(r) && /폴더에 없음/.test(r)) && !/이미받음/.test(rows[3]), "기록 회차는 이미받음 + 폴더에 없음: " + JSON.stringify(rows.slice(0, 4)));
  ok(/이미 받음 3개\(그중 폴더에 없는 기록 3개\)/.test(text) && /받을 회차 1개/.test(text), "개수 안내: " + text);
  done();
})();
