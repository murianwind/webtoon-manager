// 아카이빙 탭 구성: 아카이빙 대상 → 아카이빙 공통 설정 → 완결 자동이동 설정 → 폴더 일괄 이동 순서.
// 공통 설정은 "같은 이름 파일 처리"(설명이 붙은 선택지)와 "파일명 변경 설정" 두 부분으로 나뉘고, 카드마다 자기 저장 버튼이 있다.
// 저장은 서버에 저장된 현재 값 위에 그 카드에서 바꾼 항목만 덮어써서 보낸다 — 다른 카드의 저장하지 않은 변경이 같이 저장되지 않는다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const server = { default_base_path: "기본보관", default_dest_type: "local", conflict_policy: "skip", on_finish_unsubscribe: true, filename_template: "{title} {episode_no}", local_available: true, rclone_available: false };
  const posts = [];
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/archive/settings": (opts) => { if (opts && opts.method === "POST") { const body = JSON.parse(opts.body); posts.push(body); Object.assign(server, body); } return { ...server }; },
    "/api/archive/targets": [], "/api/archive/presets": [], "/api/archive/rclone/remotes": { remotes: [] }, "/api/archive/folders": { folders: [], current_path_selectable: true },
    "/api/webtoons": [], "/api/kakao-webtoons": [],
  };
  const { w, run } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  run("window.__p = loadArchiveSettings();"); await w.__p; await sleep(40);

  // Scenario 1: 카드 순서 — 아카이빙 대상, 아카이빙 공통 설정, 완결 자동이동 설정, 폴더 일괄 이동
  const cards = [...w.document.querySelectorAll("#archive-main-content > .settings-card > h2")].map((h) => h.textContent.trim());
  ok(JSON.stringify(cards) === JSON.stringify(["아카이빙 대상", "아카이빙 공통 설정", "완결 자동이동 설정", "폴더 일괄 이동 (1회성)"]), "카드 순서: " + JSON.stringify(cards));
  // Scenario 2: 공통 설정은 두 부분 — 같은 이름 파일 처리 / 파일명 변경 설정
  const common = [...w.document.querySelectorAll("#archive-main-content > .settings-card")].find((c) => c.querySelector("h2").textContent.trim() === "아카이빙 공통 설정");
  const sections = [...common.querySelectorAll("h3")].map((h) => h.textContent.trim());
  ok(JSON.stringify(sections) === JSON.stringify(["같은 이름 파일 처리", "파일명 변경 설정"]), "공통 설정의 두 부분: " + JSON.stringify(sections));
  ok(!!common.querySelector("#archive-preset-select") && !!common.querySelector("#archive-filename-template") && !!common.querySelector("#archive-filename-template-preview"), "파일명 변경 설정에 프리셋/편집/미리보기가 그대로 있다");

  // Scenario 3: 같은 이름 파일 처리 — 설명이 붙은 세 선택지(라디오), 서버 값으로 선택된다
  const radios = [...w.document.querySelectorAll('input[name="archive-conflict-policy"]')];
  ok(JSON.stringify(radios.map((r) => r.value)) === JSON.stringify(["skip", "overwrite", "rename"]), "선택지 세 개: " + radios.map((r) => r.value));
  ok(radios.find((r) => r.checked).value === "skip", "서버 값(건너뛰기)이 선택됨");
  const descs = [...common.querySelectorAll(".archive-option-card")].map((c) => c.textContent.replace(/\s+/g, " "));
  ok(descs.length === 3 && /건너뛰기.*그대로 두고/.test(descs[0]) && /덮어쓰기.*교체/.test(descs[1]) && /이름 바꿔서 저장.*\(2\)/.test(descs[2]), "각 선택지에 한 줄 설명: " + JSON.stringify(descs));
  ok(/info\.xml/.test(common.textContent) && /항상 덮어/.test(common.textContent), "info.xml/표지는 이 설정과 무관하게 덮어쓴다는 안내");

  // Scenario 4: 같은 이름 파일 처리 저장 — 서버 값 위에 이 항목만 바꿔서 저장(다른 항목은 그대로)
  radios.find((r) => r.value === "rename").checked = true;
  $("btn-save-archive-conflict").click(); await sleep(60);
  ok(posts.length === 1 && posts[0].conflict_policy === "rename" && posts[0].on_finish_unsubscribe === true && posts[0].default_base_path === "기본보관" && posts[0].filename_template === "{title} {episode_no}", "충돌 정책만 바뀜: " + JSON.stringify(posts[0]));
  ok($("archive-conflict-save-result").textContent === "저장했습니다.", "저장 결과 안내");

  // Scenario 5: 완결 자동이동 카드에도 자기 저장 버튼이 있고, 저장하지 않은 다른 카드의 변경은 같이 저장하지 않는다
  radios.find((r) => r.value === "overwrite").checked = true;            // 공통 설정 카드에서 바꾸기만 하고 저장은 안 함
  $("archive-on-finish-toggle").checked = false;
  $("btn-save-archive-finish").click(); await sleep(60);
  ok(posts.length === 2 && posts[1].on_finish_unsubscribe === false && posts[1].conflict_policy === "rename", "완결 설정만 저장(충돌 정책은 서버 값 유지): " + JSON.stringify(posts[1]));
  ok($("archive-finish-save-result").textContent === "저장했습니다.", "완결 설정 저장 결과 안내");
  // Scenario 6: 완결 자동이동 카드 안내 — 이동이 끝나면 그 웹툰의 아카이빙 대상 설정이 삭제된다는 것과 이동 시점
  const finish = [...w.document.querySelectorAll("#archive-main-content > .settings-card")].find((c) => c.querySelector("h2").textContent.trim() === "완결 자동이동 설정");
  ok(/이동이 끝나면/.test(finish.textContent) && /아카이빙 대상"에서 삭제/.test(finish.textContent), "완결 이동 뒤 대상 삭제 안내: " + finish.textContent.replace(/\s+/g, " ").slice(0, 300));
  // Scenario 7: 수동 완결 처리 확인 창에도 같은 안내
  const src = require("fs").readFileSync(require("path").join(__dirname, "..", "..", "app", "static", "js", "92-archive-targets.js"), "utf8");
  ok(/아카이빙 대상에서도 삭제/.test(src), "수동 완결 처리 확인 창에 대상 삭제 안내");
  done();
})();
