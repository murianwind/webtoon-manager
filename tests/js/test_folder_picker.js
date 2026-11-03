// 폴더 선택기(renderFolderPicker/renderFolderPickerContents, 90-archive.js)의 동작을 고정한다 — 아카이빙 대상/일괄 이동/기본 폴더 화면이 모두 쓰는 큰 화면 부품이다.
// 단계적으로 펼쳐진다: 접힘(요약) → 로컬/rclone 선택 → (로컬이 둘 이상이면) 루트 선택 → (rclone이면) 원격 선택 → 폴더 목록. 목록은 한 번 받은 경로를 기억하고,
// 폴더를 고르면 접혀서 "선택됨: …" 요약만 보이며, 어떤 실패에도 로컬/rclone 전환과 "처음으로 돌아가기"는 남는다.
// (이 테스트는 큰 함수를 작은 함수들로 나누기 전에 현재 동작을 그대로 고정해 둔 것이다.)
const { makeApp, sleep, idle, ok, done } = require("./harness");
(async () => {
  let settings = { local_available: true, rclone_available: true };
  let slowList = false, abortCount = 0, failSettings = null, failList = null, failCheck = null, failCreate = null, checkSelectable = true;
  const tree = { "": ["가작품", "나작품"], "가작품": ["가-1"], "나작품": [] };
  const listCalls = [], posts = [], checks = [];
  const folders = (path) => ({ folders: (tree[path] || []).map((name) => ({ name, path: path ? `${path}/${name}` : name })) });
  const q = (url) => new URL(url, "http://x").searchParams;
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/archive/settings": () => { if (failSettings) throw new Error(failSettings); return { ...settings }; },
    "/api/archive/rclone/remotes": { remotes: ["onedrive", "PLEX"] },
    "/api/archive/folders": (opts, url) => {
      if (opts && opts.method === "POST") { posts.push(["local", JSON.parse(opts.body)]); if (failCreate) throw new Error(failCreate); return {}; }
      listCalls.push(["local", q(url).get("local_root"), q(url).get("path")]);
      if (slowList) return new Promise((resolve, reject) => opts.signal.addEventListener("abort", () => { abortCount++; reject(Object.assign(new Error("aborted"), { name: "AbortError" })); })); // 응답이 안 오는 느린 목록 — 취소하면 중단된다
      if (failList) { const e = new Error(failList.message); e.name = failList.name || "Error"; throw e; }
      return folders(q(url).get("path"));
    },
    "/api/archive/rclone/folders": (opts, url) => {
      if (opts && opts.method === "POST") { posts.push(["rclone", JSON.parse(opts.body)]); if (failCreate) throw new Error(failCreate); return {}; }
      listCalls.push(["rclone", q(url).get("remote"), q(url).get("path")]); return folders(q(url).get("path"));
    },
    "/api/archive/folder-check": (opts, url) => { checks.push([q(url).get("dest_type"), q(url).get("remote"), q(url).get("path")]); if (failCheck) throw new Error(failCheck); return { selectable: checkSelectable }; },
  };
  const { w, run } = makeApp(routes); await idle();
  const alerts = []; w.alert = (message) => alerts.push(message);
  const ID = "archive-folder-target-source-picker";   // 즐겨찾기가 없는 선택기
  const $c = () => w.document.getElementById(ID);
  const buttons = () => [...$c().querySelectorAll("button")];
  const btn = (text) => buttons().find((b) => b.textContent.trim() === text);
  const labels = () => buttons().map((b) => b.textContent.trim());
  const rowNames = () => [...$c().querySelectorAll(".folder-picker-row > button:first-child")].map((b) => b.textContent.trim());
  const text = () => $c().textContent;
  const click = async (b) => { if (!b) throw new Error("버튼이 없다: " + labels()); b.click(); await idle(); };
  const render = async (options = {}, initialPath = "") => {
    w.sessionStorage.clear(); run("invalidateArchiveCaches(); _folderListCache = {}; archiveFolderPickerState = {};"); w.__sel = []; listCalls.length = 0; posts.length = 0; checks.length = 0; alerts.length = 0;
    failSettings = failList = failCheck = failCreate = null; checkSelectable = true;
    run(`window.__p = renderFolderPicker("${ID}", (value, destType, root) => window.__sel.push([value, destType, root]), ${JSON.stringify(initialPath)}, ${JSON.stringify(options)});`); await w.__p; await idle();
  };
  const active = () => [...$c().querySelectorAll(".folder-picker-choice-active")].map((b) => b.textContent.trim());

  // Scenario 1: 로컬/rclone 둘 다 쓸 수 있으면 먼저 둘 중 하나를 고르게 한다 — 고르기 전에는 폴더 목록을 요청하지도 않는다
  await render();
  ok(JSON.stringify(labels()) === JSON.stringify(["로컬 폴더", "rclone 원격"]) && listCalls.length === 0 && active().length === 0, "선택 전: " + labels() + " | 요청 " + listCalls.length);
  await click(btn("로컬 폴더"));
  ok(active().join() === "로컬 폴더" && listCalls.length === 1 && JSON.stringify(listCalls[0]) === JSON.stringify(["local", "archive", ""]), "로컬을 고르면 보관 루트의 최상위 목록을 요청: " + JSON.stringify(listCalls));
  ok(text().includes("현재 위치: /") && JSON.stringify(rowNames()) === JSON.stringify(["📁 가작품", "📁 나작품"]) && labels().filter((l) => l === "이 폴더 선택").length === 2 && !!btn("새 폴더 만들기"), "폴더 목록/현재 위치/선택/새 폴더 버튼: " + labels());
  await click(btn("로컬 폴더")); ok(listCalls.length === 1 && rowNames().length === 0 && active().length === 0, "이미 펼친 로컬을 다시 누르면 접는다(요청 없음)");
  await click(btn("rclone 원격")); ok(active().join() === "rclone 원격" && !!$c().querySelector("select") && listCalls.length === 1, "rclone을 고르면 원격 고르기부터(목록 요청은 아직)");
  await click(btn("로컬 폴더")); ok(active().join() === "로컬 폴더" && rowNames().length === 2, "로컬로 다시 전환(전환해도 이전 목록 캐시 사용: 요청 " + listCalls.length + ")");
  console.log("1) 모드 선택 OK");

  // Scenario 2: 한쪽만 쓸 수 있으면 전환 줄 없이 바로 시작 — 로컬만: 목록이 바로, rclone만: 원격 고르기가 바로. 둘 다 못 쓰면 이유를 알려 준다
  settings = { local_available: true, rclone_available: false }; await render();
  ok(!btn("로컬 폴더") && rowNames().length === 2, "로컬만: 전환 줄 없이 목록 바로: " + labels());
  settings = { local_available: false, rclone_available: true }; await render();
  ok(!btn("rclone 원격") && !!$c().querySelector("select") && listCalls.length === 0, "rclone만: 원격 고르기 바로");
  settings = { local_available: false, rclone_available: false }; await render(); ok(text().includes("로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다."), "둘 다 없음(로컬 모드): " + text());
  settings = { local_available: true, rclone_available: true };
  console.log("2) 한쪽만/둘 다 없음 OK");

  // Scenario 3: rclone 원격 — 원격을 고르면 그 원격의 최상위 목록(현재 위치 "원격:/"), "원격 다시 선택"으로 되돌아간다
  await render(); await click(btn("rclone 원격"));
  $c().querySelector("select").value = "PLEX"; await click(btn("이 원격 사용"));
  ok(JSON.stringify(listCalls[0]) === JSON.stringify(["rclone", "PLEX", ""]) && text().includes("현재 위치: PLEX:/"), "고른 원격의 목록: " + JSON.stringify(listCalls));
  await click(btn("⬅ 원격 다시 선택")); ok(!!$c().querySelector("select") && rowNames().length === 0, "원격 다시 선택");
  // 원격을 다시 고르면 그 원격의 목록을 항상 새로 받는다(앱 밖에서 폴더를 만들었을 수 있다 — 예전에 캐시 때문에 새 폴더가 안 보인 적이 있다). 하위 폴더에 들어갔다가 돌아와도 처음 위치부터
  await click(btn("이 원격 사용")); await click(btn("📁 가작품")); await click(btn("⬅ 원격 다시 선택"));
  const before = listCalls.length; $c().querySelector("select").value = "PLEX"; await click(btn("이 원격 사용"));
  ok(listCalls.length === before + 1 && JSON.stringify(listCalls.at(-1)) === JSON.stringify(["rclone", "PLEX", ""]), "다시 고르면 최상위 목록을 새로 요청(캐시 무효화 + 경로 초기화): " + JSON.stringify(listCalls.slice(before)));
  console.log("3) rclone 원격 OK");

  // Scenario 4: 폴더 탐색 — 폴더 이름을 누르면 그 안으로(요청, 현재 위치 갱신, 상위 폴더 버튼), 상위로 올라가면 이미 본 목록은 다시 요청하지 않는다
  await render({}); await click(btn("로컬 폴더")); listCalls.length = 0;
  await click(btn("📁 가작품")); ok(text().includes("현재 위치: /가작품") && JSON.stringify(rowNames()) === JSON.stringify(["📁 가-1"]) && !!btn("⬆ 상위 폴더") && listCalls.length === 1 && listCalls[0][2] === "가작품", "하위로: " + JSON.stringify(listCalls));
  await click(btn("⬆ 상위 폴더")); ok(text().includes("현재 위치: /") && rowNames().length === 2 && !btn("⬆ 상위 폴더") && listCalls.length === 1, "상위로(이미 본 목록은 재요청 없음): " + listCalls.length);
  await click(btn("📁 가작품")); await click(btn("📁 가-1")); ok(listCalls.length === 2 && listCalls[1][2] === "가작품/가-1", "두 단계 아래: " + JSON.stringify(listCalls));
  await click(btn("⬆ 상위 폴더")); ok(text().includes("현재 위치: /가작품") && !text().includes("가작품/가-1"), "한 단계씩 올라간다");
  console.log("4) 탐색 OK");

  // Scenario 5: 선택(확인 없이: 일괄 이동용 skipExistingCheck) — 값/종류/루트를 알리고 접혀서 "선택됨: …"만 보인다, "변경"으로 다시 펼친다. 선택한 폴더는 목록에서 ✅ 표시
  await render({ skipExistingCheck: true }); await click(btn("로컬 폴더"));
  await click([...$c().querySelectorAll(".folder-picker-row")][1].querySelector("button:nth-child(2)"));
  ok(JSON.stringify(w.__sel) === JSON.stringify([["나작품", "local", "archive"]]) && checks.length === 0, "선택 알림(값, 종류, 루트), 확인 요청 없음: " + JSON.stringify(w.__sel));
  ok(text().includes("선택됨: 나작품") && !!btn("변경") && rowNames().length === 0, "접혀서 요약: " + text());
  await click(btn("변경")); ok(rowNames().includes("📁 나작품 ✅") && rowNames().includes("📁 가작품"), "다시 펼치면 선택한 폴더에 ✅: " + rowNames());
  console.log("5) 선택/요약 OK");

  // Scenario 6: 선택(확인 있음: 아카이빙 대상용) — 비어 있으면 바로 선택, 이미 파일이 있으면 그 자리에서 경고와 "그래도 선택", 확인 실패면 알림 후 버튼 복구
  await render(); await click(btn("로컬 폴더"));
  await click([...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"));
  ok(JSON.stringify(checks[0]) === JSON.stringify(["local", "", "가작품"]) && JSON.stringify(w.__sel) === JSON.stringify([["가작품", "local", "archive"]]), "비어 있으면 바로 선택: " + JSON.stringify(checks) + JSON.stringify(w.__sel));
  await render(); await click(btn("로컬 폴더")); checkSelectable = false;
  await click([...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"));
  ok(w.__sel.length === 0 && text().includes('⚠ "가작품"엔 이미 파일이 있습니다.') && !!btn("그래도 선택"), "파일이 있으면 경고: " + text());
  await click(btn("그래도 선택")); ok(JSON.stringify(w.__sel) === JSON.stringify([["가작품", "local", "archive"]]), "그래도 선택");
  await render(); await click(btn("로컬 폴더")); failCheck = "확인 서버 오류";
  const selectBtn = [...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"); await click(selectBtn);
  ok(alerts.join() === "확인 실패: 확인 서버 오류" && selectBtn.textContent === "이 폴더 선택" && selectBtn.disabled === false && w.__sel.length === 0, "확인 실패: 알림 + 버튼 복구: " + alerts);
  console.log("6) 확인 절차 OK");

  // Scenario 7: rclone 선택은 "원격:경로" 값과 rclone 종류로 알리고(루트는 없음), 확인 요청에 원격을 담는다
  await render({ skipExistingCheck: true }); await click(btn("rclone 원격")); $c().querySelector("select").value = "onedrive"; await click(btn("이 원격 사용"));
  await click([...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"));
  ok(JSON.stringify(w.__sel) === JSON.stringify([["onedrive:가작품", "rclone", undefined]]) && text().includes("선택됨: 가작품"), "rclone 선택: " + JSON.stringify(w.__sel));
  await render(); await click(btn("rclone 원격")); await click(btn("이 원격 사용")); await click([...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"));
  ok(JSON.stringify(checks[0]) === JSON.stringify(["rclone", "onedrive", "가작품"]), "확인 요청에 원격: " + JSON.stringify(checks));
  console.log("7) rclone 선택 OK");

  // Scenario 8: 새 폴더 만들기 — 빈 이름은 무시, 이름을 넣으면 현재 위치에 만들고(요청 본문: 로컬은 path+root, rclone은 remote+path) 같은 위치에 머물며 목록을 새로 받는다. 실패하면 알림 후 입력 복구
  await render(); await click(btn("로컬 폴더")); const nameInput = () => $c().querySelector(".folder-picker-new-row input");
  await click(btn("새 폴더 만들기")); ok(posts.length === 0, "빈 이름은 무시");
  nameInput().value = "  새작품  "; listCalls.length = 0; await click(btn("새 폴더 만들기"));
  ok(JSON.stringify(posts) === JSON.stringify([["local", { path: "새작품", root: "archive" }]]) && listCalls.length === 1 && nameInput().value === "", "만들기: " + JSON.stringify(posts) + " 재조회 " + listCalls.length);
  await click(btn("📁 가작품")); nameInput().value = "하위"; await click(btn("새 폴더 만들기")); ok(posts[1][1].path === "가작품/하위" && text().includes("현재 위치: /가작품"), "현재 위치 아래에 만들고 그대로 머문다: " + JSON.stringify(posts[1]));
  failCreate = "이미 있음"; nameInput().value = "실패"; await click(btn("새 폴더 만들기")); ok(alerts.join() === "이미 있음" && btn("새 폴더 만들기").disabled === false && nameInput().disabled === false && nameInput().value === "실패", "실패: 알림 + 입력 복구: " + alerts);
  await render(); await click(btn("rclone 원격")); await click(btn("이 원격 사용")); nameInput().value = "원격새폴더"; await click(btn("새 폴더 만들기"));
  ok(JSON.stringify(posts) === JSON.stringify([["rclone", { remote: "onedrive", path: "원격새폴더" }]]), "rclone 만들기 본문: " + JSON.stringify(posts));
  console.log("8) 새 폴더 OK");

  // Scenario 9: 로컬 루트가 둘 이상이면(일괄 이동) 로컬 모드에서 루트를 고르게 한다 — 고르기 전에는 목록 없음, 루트를 바꾸면 처음 위치부터, 다시 누르면 접힘
  await render({ localRoots: ["archive", "download"], skipExistingCheck: true }); await click(btn("로컬 폴더"));
  ok(labels().includes("보관 폴더(ARCHIVE_ROOT)") && labels().includes("다운로드 폴더(네이버)") && rowNames().length === 0 && listCalls.length === 0, "루트 선택 전에는 목록 없음: " + labels());
  await click(btn("다운로드 폴더(네이버)")); ok(listCalls[0][1] === "download" && rowNames().length === 2 && active().includes("다운로드 폴더(네이버)"), "루트를 고르면 그 루트의 목록: " + JSON.stringify(listCalls));
  await click(btn("📁 가작품")); await click(btn("보관 폴더(ARCHIVE_ROOT)")); ok(listCalls.at(-1)[1] === "archive" && listCalls.at(-1)[2] === "", "루트를 바꾸면 처음 위치부터: " + JSON.stringify(listCalls.at(-1)));
  await click(btn("보관 폴더(ARCHIVE_ROOT)")); ok(rowNames().length === 0, "활성 루트를 다시 누르면 접힘");
  await click(btn("다운로드 폴더(네이버)")); await click([...$c().querySelectorAll(".folder-picker-row")][0].querySelector("button:nth-child(2)"));
  ok(JSON.stringify(w.__sel) === JSON.stringify([["가작품", "local", "download"]]), "선택 알림에 고른 루트: " + JSON.stringify(w.__sel));
  console.log("9) 루트 선택 OK");

  // Scenario 10: 실패 — 설정 조회 실패는 오류 문구, 목록 조회 실패는 오류 문구 + "처음으로 돌아가기"(저장된 위치를 지우고 처음부터), 직접 취소(AbortError)는 오류로 안 친다
  await render(); w.sessionStorage.clear(); run("archiveFolderPickerState = {}; invalidateArchiveCaches();"); failSettings = "설정 서버 오류";
  run(`window.__p = renderFolderPicker("${ID}", () => {}, "", {});`); await w.__p; await idle(); ok(!!$c().querySelector(".error") && text().includes("설정 서버 오류"), "설정 조회 실패 문구: " + text());
  await render(); await click(btn("로컬 폴더")); await click(btn("📁 가작품")); run("_folderListCache = {};"); failList = { message: "목록 서버 오류" };
  await click(btn("📁 가-1")); ok(text().includes("목록 서버 오류") && !!btn("⬅ 처음으로 돌아가기") && w.sessionStorage.getItem(`folderPickerState:${ID}`) === null && !!btn("로컬 폴더"), "목록 실패: 문구 + 처음으로 + 저장 위치 삭제 + 전환 버튼 유지: " + labels());
  failList = null; await click(btn("⬅ 처음으로 돌아가기")); ok(text().includes("현재 위치: /") && !text().includes("가작품/가-1") && rowNames().length === 2, "처음으로 돌아가면 최상위 목록: " + text());
  await render(); await click(btn("로컬 폴더")); await click(btn("📁 가작품")); run("_folderListCache = {};"); failList = { message: "취소됨", name: "AbortError" }; await click(btn("📁 가-1"));
  ok(!$c().querySelector(".error") && !btn("⬅ 처음으로 돌아가기"), "직접 취소(AbortError)는 오류로 안 친다");
  failList = null;
  console.log("10) 실패 처리 OK");

  // Scenario 11: 다시 그려도 목록의 스크롤 위치를 유지한다(방금 고른 게 보이던 자리에서 벗어나지 않게) / 같은 탭에서는 보던 위치를 기억해 다시 열어도 그 자리
  await render(); await click(btn("로컬 폴더")); $c().querySelector(".folder-picker-list").scrollTop = 120; run(`renderFolderPickerContents("${ID}", () => {});`); await idle();
  ok($c().querySelector(".folder-picker-list").scrollTop === 120, "스크롤 위치 유지: " + $c().querySelector(".folder-picker-list").scrollTop);
  await click(btn("📁 가작품")); run("_folderListCache = {}; archiveFolderPickerState = {};");
  run(`window.__p = renderFolderPicker("${ID}", () => {}, "", {});`); await w.__p; await idle();
  ok(text().includes("현재 위치: /가작품"), "저장된 위치로 다시 연다: " + text());
  // 시작 경로(initialPath, 세 번째 인자) — 지금 동작: 로컬만 쓸 수 있어 전환 줄이 없으면 그 위치에서 시작한다. 둘 다 쓸 수 있으면 로컬/rclone을 고르는 순간 처음 위치로 초기화된다
  // (실제 호출자는 모두 ""를 넘기므로 영향은 없다 — 이 동작을 바꾸려면 이 테스트를 같이 바꿔야 한다)
  settings = { local_available: true, rclone_available: false }; await render({ skipExistingCheck: true }, "가작품");
  ok(text().includes("현재 위치: /가작품") && listCalls[0][2] === "가작품", "로컬만: 시작 경로에서 시작: " + text());
  settings = { local_available: true, rclone_available: true }; await render({ skipExistingCheck: true }, "가작품"); await click(btn("로컬 폴더"));
  ok(text().includes("현재 위치: /") && !text().includes("현재 위치: /가작품") && listCalls[0][2] === "", "둘 다 가능: 모드를 고르면 처음 위치로: " + text());
  console.log("11) 스크롤/기억 OK");

  // Scenario 12: 느린 목록 — 불러오는 중에도 "취소하고 돌아가기"를 바로 보여 주고, 누르면 요청을 중단한다(오류로 안 친다). 이전에 저장된 모드가 지금 환경에서 못 쓰는 것이면(rclone 설정이 사라짐) 이유를 알려 준다
  await render(); await click(btn("로컬 폴더")); slowList = true; run("_folderListCache = {};"); abortCount = 0;
  btn("📁 가작품").click(); await sleep(30);                                                                                       // (요청이 일부러 끝나지 않으므로 idle로 기다리지 않는다)
  ok(text().includes("⏳ 폴더 목록을 불러오는 중입니다...") && !!btn("취소하고 돌아가기"), "느린 목록: 로딩 문구와 취소 버튼: " + text());
  slowList = false; btn("취소하고 돌아가기").click(); await sleep(30);
  ok(abortCount === 1, "취소하면 요청을 중단한다: 중단 " + abortCount);                                                                // (중단이 안 되면 요청이 영원히 남아 아래 idle이 실패한다 — 그 전에 여기서 먼저 알린다)
  await idle(); ok(!$c().querySelector(".error"), "취소는 오류로 안 친다");
  w.sessionStorage.setItem(`folderPickerState:${ID}`, JSON.stringify({ mode: "rclone", path: "", remote: "onedrive", modeConfirmed: true, rootConfirmed: false, localRoot: "archive", expanded: true }));
  settings = { local_available: true, rclone_available: false }; run("archiveFolderPickerState = {}; invalidateArchiveCaches();");
  run(`window.__p = renderFolderPicker("${ID}", () => {}, "", {});`); await w.__p; await idle();
  ok(text().includes("rclone 설정 파일이 등록되어 있지 않습니다."), "저장된 rclone 모드인데 rclone을 못 쓰는 환경: " + text());
  settings = { local_available: true, rclone_available: true };
  console.log("12) 느린 목록/저장 모드 불가 OK");
  done();
})();
