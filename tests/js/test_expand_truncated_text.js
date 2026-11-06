// 잘려서(…) 안 보이는 글자를 눌러서 펼친다 — 눌러서 펼쳐지는 항목이 아닌 목록(다운로드 이력, 아카이빙 이력, 아카이빙 대상, 카카오 분석 표의 부제)에서
// 좁은 화면에서 내용이 "…"로 잘려 읽을 수 없던 것을 그 자리에서 전체 내용으로 보여 준다(여러 줄로 줄바꿈). 다시 누르면 접힌다.
// 실제로 잘렸을 때만(scrollWidth > clientWidth) 동작하고, 글자를 드래그해 복사하는 중이거나 이미 눌러서 펼쳐지는 항목(실행 이력 등)이면 건드리지 않는다.
// jsdom은 레이아웃을 계산하지 않아서 잘림은 scrollWidth/clientWidth를 직접 정해서 흉내 낸다. 스타일 규칙이 실제 파일에 들어 있는지는 CSS 파일을 직접 읽어 확인한다.
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const routes = {
    "/api/episode-history": { items: [{ id: 1, title_name: "긴 제목 작품", episode_no: 12, subtitle: "아주아주 긴 부제목이 들어 있는 회차", downloaded_at: "2026-11-04T01:00:00+00:00", status: "success", error_msg: "" }], page: 1, total: 1, page_size: 20 },
    "/api/archive/history": { items: [{ id: 1, title_name: "보관 작품", file_name: "0012_아주아주긴파일이름이들어있는회차.zip", archived_at: "2026-11-04T01:00:00+00:00", trigger_type: "periodic" }], page: 1, total: 1, page_size: 20 },
    "/api/archive/targets": [{ title_id: "1", title_name: "대상 작품", source_type: "webtoon", dest_type: "local", dest_base_path: "웹툰보관/아주/긴/보관/위치/폴더", enabled: true, filename_template_preset_name: null, platform: "naver" }],
    "/api/jobs/history": [{ id: 1, job_name: "discovery", started_at: "2026-11-04T01:00:00+00:00", status: "success", log: ["2026-11-04T01:00:00+00:00 — 시작"] }],
  };
  const { w, run } = makeApp(routes); await idle();
  const $ = (id) => w.document.getElementById(id);
  const fit = (el, truncated) => { Object.defineProperty(el, "scrollWidth", { value: truncated ? 400 : 100, configurable: true }); Object.defineProperty(el, "clientWidth", { value: 100, configurable: true }); };
  const tap = (el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const expanded = (el) => el.classList.contains("text-expanded");
  const withSelection = (text) => { w.getSelection = () => ({ toString: () => text }); };
  withSelection("");

  // Scenario 1: 다운로드 이력 — 잘린 "회차 부제 · 시각" 칸을 누르면 펼쳐지고, 다시 누르면 접힌다. 잘리지 않았으면 아무 일도 없다. 삭제 버튼 등 다른 곳은 영향 없다
  run("loadEpisodeHistory(1);"); await idle();
  let cell = $("episode-history-list").querySelector(".job-history-time"); ok(cell && cell.textContent.includes("아주아주 긴 부제목"), "다운로드 이력 칸");
  fit(cell, false); tap(cell); ok(!expanded(cell), "잘리지 않았으면 그대로");
  fit(cell, true); tap(cell); ok(expanded(cell), "잘렸으면 눌러서 펼친다");
  fit(cell, false); tap(cell); ok(!expanded(cell), "펼친 것은 (이제 안 잘려 보여도) 다시 누르면 접힌다");
  fit(cell, true); tap(cell); tap(cell); ok(!expanded(cell), "두 번 누르면 접힌다");
  const rowName = $("episode-history-list").querySelector(".job-history-name"); fit(rowName, true); tap(rowName); ok(!expanded(rowName), "제목 칸 등 대상이 아닌 곳은 그대로");

  // Scenario 2: 글자를 선택(드래그)하는 중이면 펼치지 않는다 — 복사하려고 길게 눌러 고르는 동작을 방해하지 않게
  fit(cell, true); withSelection("부제목"); tap(cell); ok(!expanded(cell), "선택 중이면 안 펼친다"); withSelection("");

  // Scenario 3: 아카이빙 이력(파일 이름 · 시각), 아카이빙 대상(폴더 → 보관 위치)도 같다
  run("loadArchiveHistory(1);"); await idle();
  cell = $("archive-history-list").querySelector(".job-history-time"); ok(cell && cell.textContent.includes("아주아주긴파일이름"), "아카이빙 이력 칸"); fit(cell, true); tap(cell); ok(expanded(cell), "아카이빙 이력 펼침");
  run("loadArchiveTargetList();"); await idle();
  cell = $("archive-target-list").querySelector(".job-history-time"); ok(cell && cell.textContent.includes("웹툰보관"), "아카이빙 대상 칸"); fit(cell, true); tap(cell); ok(expanded(cell), "아카이빙 대상 펼침");

  // Scenario 4: 눌러서 펼쳐지는 항목(실행 이력)은 건드리지 않는다 — 항목을 누르면 원래대로 로그가 펼쳐지고, 시각 칸은 글자 펼침 대상이 아니다
  run("loadJobHistory();"); await idle();
  const entry = $("job-history-list").querySelector(".job-history-entry"), time = entry.querySelector(".job-history-time");
  fit(time, true); tap(time); ok(!expanded(time) && entry.classList.contains("expanded"), "실행 이력: 글자는 안 펼치고 항목(로그)이 펼쳐진다");

  // Scenario 5: 카카오 분석 표의 부제 칸 — 잘렸으면 눌러서 펼친다(표의 다른 칸/체크박스는 그대로)
  const table = w.document.createElement("table"); table.id = "kakao-manual-table";
  table.innerHTML = '<tbody><tr><td><input type="checkbox"></td><td>1</td><td>아주아주 긴 회차 부제목 아주아주 긴 회차 부제목</td><td>무료</td></tr></tbody>'; w.document.body.appendChild(table);
  const subtitle = table.querySelector("td:nth-child(3)"), number = table.querySelector("td:nth-child(2)");
  fit(subtitle, true); tap(subtitle); ok(expanded(subtitle), "분석 표 부제 펼침"); fit(number, true); tap(number); ok(!expanded(number), "다른 칸은 그대로");
  tap(subtitle); ok(!expanded(subtitle), "다시 누르면 접힘");

  // Scenario 6: 목록을 다시 불러와 새로 그려도 그대로 동작한다(이벤트를 항목마다 달지 않고 한 곳에서 받는다)
  run("loadEpisodeHistory(1);"); await idle(); cell = $("episode-history-list").querySelector(".job-history-time"); fit(cell, true); tap(cell); ok(expanded(cell), "다시 그린 목록도 펼침");

  // Scenario 7: 스타일 — 펼친 글자는 줄바꿈되고 잘림이 풀린다(긴 문자열도 화면 폭에 맞게 꺾인다), 눌러 볼 수 있는 칸은 손가락 모양
  const css = require("fs").readFileSync(require("path").join(__dirname, "../../app/static/style.css"), "utf8");
  const block = (selector) => { const m = css.match(new RegExp(selector.replace(/[.#>]/g, "\\$&") + "\\s*\\{([^}]*)\\}")); return m ? m[1] : ""; };
  const expandedBlock = block(".text-expanded");
  ok(/white-space:\s*normal/.test(expandedBlock) && /overflow:\s*visible/.test(expandedBlock) && /overflow-wrap:\s*anywhere/.test(expandedBlock) && /text-overflow:\s*clip/.test(expandedBlock), "펼친 글자 스타일: " + expandedBlock.replace(/\s+/g, " "));
  ok(/cursor:\s*pointer/.test(css.match(/\.job-history-summary \.job-history-time\s*\{[^}]*\}/)[0]), "잘리는 칸은 눌러 볼 수 있다는 표시(손가락)");
  done();
})();
