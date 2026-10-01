// 카카오 수동 다운로드 분석 화면 — 사이트 회차 수와 가져온 수가 다르면 경고, "N일 후 무료" 날짜 표시
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const eps = (n, extra = {}) => Array.from({ length: n }, (_, i) => ({ number: i + 1, subtitle: `${i + 1}화`, state: "free", expire: null, free_at: null, downloaded: false, before_start: false, selectable: true, ...extra }));
  const base = { series_id: 68757181, title: "탈조클럽", folder: "/dl/탈조클럽", mode: "new_folder", existing_count: 0, marker: null, to_download_count: 31, locked_count: 0, downloaded_count: 0, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: null, tickets: null, site_total: 36, listed_count: 31, hidden_count: 0, excluded_video_count: 0, episodes: eps(31) };
  let cur = base;
  const routes = { "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/kakao-manual/analyze": () => cur };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("manual-download");`); $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  const analyze = async (a) => { cur = a; $("manual-query").value = "68757181"; $("btn-manual-analyze").click(); await sleep(40); return $("kakao-manual-summary").textContent; };

  let t = await analyze(base);
  ok(t.includes("사이트 회차는 36개인데 31개만 가져왔습니다"), "사이트 36개인데 31개만 가져오면 경고가 나옴: " + t.slice(-90));
  t = await analyze({ ...base, hidden_count: 5, listed_count: 31 });
  ok(t.includes("사이트 회차는 36개인데 31개만 가져왔습니다") && t.includes("숨김 처리된 회차 5개"), "숨김 회차 수도 함께 보여 줌");
  t = await analyze({ ...base, site_total: 36, listed_count: 35, excluded_video_count: 1 });
  ok(!t.includes("가져왔습니다"), "동영상으로 뺀 회차까지 더해 맞으면 경고 없음");
  t = await analyze({ ...base, site_total: 31, listed_count: 31 });
  ok(!t.includes("가져왔습니다"), "같으면 경고 없음");
  t = await analyze({ ...base, site_total: 0 });
  ok(!t.includes("가져왔습니다"), "사이트 회차 수를 모르면(0) 경고 없음");

  await analyze({ ...base, episodes: [...eps(2), { number: 3, subtitle: "3화", state: "locked", expire: null, free_at: "2026-10-07", downloaded: false, before_start: false, selectable: false }, { number: 4, subtitle: "4화", state: "locked", expire: null, free_at: null, downloaded: false, before_start: false, selectable: false }], site_total: 4, listed_count: 4 });
  const cell = (r) => w.document.querySelectorAll("#kakao-manual-tbody tr")[r].children[4].textContent.trim();
  ok(cell(2) === "2026-10-07 무료" && cell(3) === "" && cell(0) === "", "잠긴 회차는 무료가 되는 날짜를 '대여 만료' 칸에 '2026-10-07 무료'로 보여 줌(없으면 빈칸)");
  done();
})();
