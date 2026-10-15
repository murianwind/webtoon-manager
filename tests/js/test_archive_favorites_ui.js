// 보관할 폴더 즐겨찾기(폴더 선택기): 보관 쪽 선택기에만 즐겨찾기 버튼 줄이 나오고(원본/미리보기 선택기에는 없음),
// 누르면 그 폴더의 목록이 바로 열리며(원격 고르기를 건너뜀), 목록 화면의 별 버튼으로 추가/삭제하고 ✕로도 삭제한다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let favorites = [
    { id: "f1", label: "onedrive · 02 시리즈", dest_type: "rclone", root: "onedrive", path: "개인 자료/Webtoon/02 시리즈" },
    { id: "f2", label: "로컬 · 카카오", dest_type: "local", root: "archive", path: "카카오" },
    { id: "f3", label: "로컬 · 받은것", dest_type: "local", root: "download", path: "받은것" },
  ];
  const calls = [];
  let settings = { local_available: false, rclone_available: true, conflict_policy: "skip", on_finish_unsubscribe: false, default_base_path: "", default_dest_type: "local", filename_template: "" };
  const folders = (remote, path) => ({ folders: (path === "개인 자료/Webtoon/02 시리즈" ? ["도굴왕", "개미"] : ["01 작가", "02 시리즈"]).map((n) => ({ name: n, path: path ? `${path}/${n}` : n })), current_path_selectable: true });
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/archive/settings": () => ({ ...settings }), "/api/archive/rclone/remotes": { remotes: ["PLEX", "onedrive"] },
    "/api/archive/rclone/folders": (opts, url) => { const q = new URL(url, "http://x").searchParams; calls.push(["rclone", q.get("remote"), q.get("path")]); return folders(q.get("remote"), q.get("path")); },
    "/api/archive/folders": (opts, url) => { const q = new URL(url, "http://x").searchParams; calls.push(["local", q.get("local_root"), q.get("path")]); return { folders: [], current_path_selectable: true }; },
    "/api/archive/favorites": (opts) => {
      if (opts && opts.method === "POST") { const b = JSON.parse(opts.body); calls.push(["POST", b]); const f = { id: "n" + (favorites.length + 1), label: `${b.dest_type === "local" ? "로컬" : b.root} · ${b.path.split("/").pop() || "최상위"}`, ...b }; favorites.push(f); return f; }
      return favorites;
    },
  };
  // 삭제(DELETE /api/archive/favorites/<id>)는 접두사라 harness가 못 찾는다 — fetch를 감싸서 처리
  const { w, run } = makeApp(routes); await sleep(60);
  const baseFetch = w.fetch;
  w.fetch = async (url, opts = {}) => {
    const m = String(url).match(/^\/api\/archive\/favorites\/(\w+)$/);
    if (m && opts.method === "DELETE") { calls.push(["DELETE", m[1]]); favorites = favorites.filter((f) => f.id !== m[1]); return { ok: true, status: 200, json: async () => ({ status: "ok" }), text: async () => "{}" }; }
    return baseFetch(url, opts);
  };
  const $ = (id) => w.document.getElementById(id);
  const chips = (id) => [...$(id).querySelectorAll(".folder-favorite-chip")];
  const chipLabels = (id) => chips(id).map((c) => c.querySelector(".folder-favorite-name").textContent);
  const render = async (id, options) => { run(`window.__p = renderFolderPicker("${id}", () => {}, "", ${JSON.stringify(options || {})});`); await w.__p; await sleep(80); };
  // 선택기는 한 번 그려지면 위치를 sessionStorage에 기억하므로, 시나리오마다 비운다
  const reset = () => w.sessionStorage.clear();

  // Scenario 1: 보관 쪽 선택기(rclone만 쓸 수 있는 환경)에는 즐겨찾기 버튼 줄이 나온다 — 로컬 즐겨찾기는 로컬을 못 쓰는 환경이라 빠진다. 전체 경로는 마우스를 올리면 보인다
  await render("archive-folder-target-dest-picker");
  ok(JSON.stringify(chipLabels("archive-folder-target-dest-picker")) === JSON.stringify(["onedrive · 02 시리즈"]), "보관 선택기 즐겨찾기: " + JSON.stringify(chipLabels("archive-folder-target-dest-picker")));
  ok(chips("archive-folder-target-dest-picker")[0].title === "onedrive:/개인 자료/Webtoon/02 시리즈", "전체 경로가 title에: " + chips("archive-folder-target-dest-picker")[0].title);
  ok(!!$("archive-folder-target-dest-picker").querySelector("select"), "즐겨찾기와 별개로 원격 고르기는 그대로 있다");
  // Scenario 2: 원본 폴더 선택기와 파일명 미리보기 선택기에는 즐겨찾기가 없다(보관할 폴더 전용)
  reset(); await render("archive-folder-target-source-picker", { skipExistingCheck: true }); await render("archive-filename-template-preview-folder-picker", { skipExistingCheck: true });
  ok(chips("archive-folder-target-source-picker").length === 0 && chips("archive-filename-template-preview-folder-picker").length === 0, "원본/미리보기 선택기에는 즐겨찾기 줄이 없다");
  // Scenario 3: 즐겨찾기를 누르면 원격 고르기 없이 그 폴더의 목록이 바로 열린다(현재 위치 표시, 해당 경로로 조회)
  reset(); await render("archive-target-folder-picker");
  calls.length = 0; chips("archive-target-folder-picker")[0].querySelector(".folder-favorite-name").click(); await sleep(80);
  ok(calls.some((c) => c[0] === "rclone" && c[1] === "onedrive" && c[2] === "개인 자료/Webtoon/02 시리즈"), "그 경로로 목록 조회: " + JSON.stringify(calls));
  ok(/현재 위치: onedrive:\/개인 자료\/Webtoon\/02 시리즈/.test($("archive-target-folder-picker").textContent) && /도굴왕/.test($("archive-target-folder-picker").textContent) && !$("archive-target-folder-picker").querySelector(".registry-add-row select"), "목록 화면으로 바로 이동");
  ok(chips("archive-target-folder-picker")[0].classList.contains("folder-favorite-chip-active"), "지금 위치인 즐겨찾기는 강조");
  // Scenario 4: 목록 화면의 별 버튼 — 이미 즐겨찾기한 폴더는 "즐겨찾기됨", 누르면 삭제
  const star = () => $("archive-target-folder-picker").querySelector(".folder-favorite-star");
  ok(/즐겨찾기됨/.test(star().textContent), "이미 즐겨찾기한 폴더 표시: " + star().textContent);
  calls.length = 0; star().click(); await sleep(80);
  ok(calls.some((c) => c[0] === "DELETE" && c[1] === "f1") && chips("archive-target-folder-picker").length === 0 && /즐겨찾기에 추가/.test(star().textContent), "별을 누르면 삭제: " + JSON.stringify(calls));
  // Scenario 5: 다른 폴더로 들어가서 별 버튼으로 추가 — 선택기의 현재 위치(종류/원격/경로)로 저장하고 버튼 줄에 나타난다
  $("archive-target-folder-picker").querySelector(".folder-picker-row button").click(); await sleep(80);        // "도굴왕" 폴더로 들어감
  calls.length = 0; star().click(); await sleep(100);
  const post = calls.find((c) => c[0] === "POST");
  ok(post && post[1].dest_type === "rclone" && post[1].root === "onedrive" && post[1].path === "개인 자료/Webtoon/02 시리즈/도굴왕", "현재 위치로 추가 요청: " + JSON.stringify(post));
  ok(JSON.stringify(chipLabels("archive-target-folder-picker")) === JSON.stringify(["onedrive · 도굴왕"]) && /즐겨찾기됨/.test(star().textContent), "추가한 즐겨찾기가 줄에 나타나고 별이 바뀜");
  // Scenario 6: ✕로 삭제 — 목록 화면이 아닌 처음 화면에서도 된다
  const del = $("archive-target-folder-picker").querySelector(".folder-favorite-remove");
  calls.length = 0; del.click(); await sleep(80);
  ok(calls.some((c) => c[0] === "DELETE") && chips("archive-target-folder-picker").length === 0, "✕로 삭제");
  // Scenario 7: 로컬과 rclone을 둘 다 쓸 수 있으면 로컬 즐겨찾기도 나온다 — 누르면 로컬 모드로 그 루트/경로가 열린다. 일괄 이동 목적지(루트 선택 가능)는 두 루트의 즐겨찾기를 모두 보인다
  settings = { ...settings, local_available: true, rclone_available: true };
  run("invalidateArchiveCaches(); invalidateArchiveFavorites();"); favorites = [
    { id: "f2", label: "로컬 · 카카오", dest_type: "local", root: "archive", path: "카카오" },
    { id: "f3", label: "로컬 · 받은것", dest_type: "local", root: "download", path: "받은것" },
    { id: "f1", label: "onedrive · 02 시리즈", dest_type: "rclone", root: "onedrive", path: "개인 자료/Webtoon/02 시리즈" },
  ];
  reset(); await render("archive-target-folder-picker");
  ok(JSON.stringify(chipLabels("archive-target-folder-picker")) === JSON.stringify(["로컬 · 카카오", "onedrive · 02 시리즈"]), "일반 보관 선택기: 보관 루트의 로컬 + rclone: " + JSON.stringify(chipLabels("archive-target-folder-picker")));
  reset(); await render("bulk-move-dest-picker", { skipExistingCheck: true, localRoots: ["archive", "download"] });
  ok(JSON.stringify(chipLabels("bulk-move-dest-picker")) === JSON.stringify(["로컬 · 카카오", "로컬 · 받은것", "onedrive · 02 시리즈"]), "일괄 이동 목적지: 두 로컬 루트 모두: " + JSON.stringify(chipLabels("bulk-move-dest-picker")));
  reset(); await render("archive-target-folder-picker");
  calls.length = 0; chips("archive-target-folder-picker")[0].querySelector(".folder-favorite-name").click(); await sleep(80);
  ok(calls.some((c) => c[0] === "local" && c[1] === "archive" && c[2] === "카카오"), "로컬 즐겨찾기는 로컬 모드로 열림: " + JSON.stringify(calls));
  // Scenario 8: 즐겨찾기가 하나도 없으면 줄 자체가 나오지 않는다
  favorites = []; run("invalidateArchiveFavorites();"); reset(); await render("archive-target-folder-picker");
  ok(chips("archive-target-folder-picker").length === 0 && !$("archive-target-folder-picker").querySelector(".folder-favorites-row"), "즐겨찾기 없음: 줄이 없다");
  done();
})();
