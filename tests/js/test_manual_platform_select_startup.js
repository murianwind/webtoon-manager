// 새로고침으로 수동 다운로드 탭이 바로 복원돼도, "카카오 사용 여부"가 늦게 도착하면 플랫폼(네이버/카카오페이지) 선택이 나중에라도 나타난다.
// (예전엔 탭을 복원하는 순간의 값(아직 꺼짐)만 보고 숨긴 채로 그려서, 탭을 다시 눌러야 나왔다)
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const base = { "/api/settings/webtoon-server": { webtoon_server_url: "" } };

  // Scenario 1: 앱이 시작되자마자(설정 응답이 오기 전에) 수동 다운로드 탭이 복원된다 → 응답이 오면 선택이 나타난다
  let app = makeApp({ ...base, "/api/settings/kakao-webtoons-enabled": { enabled: true } });
  let select = app.w.document.getElementById("manual-platform");
  app.run(`switchToTab("manual-download");`);                       // 99-main.js가 저장된 탭을 복원하는 것과 같은 순서(응답 전)
  ok(select.classList.contains("hidden"), "복원 직후에는 아직 설정을 몰라서 숨겨져 있다(예전과 같음)");
  await sleep(60);
  ok(!select.classList.contains("hidden"), "설정 응답이 오면 플랫폼 선택이 나타난다 — 탭을 다시 누를 필요 없음");

  // Scenario 2: 카카오를 쓰지 않는 설정이면 계속 숨겨져 있고, 네이버로 동작한다
  app = makeApp({ ...base, "/api/settings/kakao-webtoons-enabled": { enabled: false } });
  select = app.w.document.getElementById("manual-platform");
  app.run(`switchToTab("manual-download");`); await sleep(60);
  ok(select.classList.contains("hidden") && app.w.eval("currentManualPlatform()") === "naver", "카카오를 안 쓰면 선택은 숨겨지고 네이버로 동작");

  // Scenario 3: 설정 화면에서 카카오 사용을 켜고 끄면, 수동 다운로드 화면의 선택도 따라간다
  app.run(`setKakaoWebtoonsEnabled(true);`);
  ok(!select.classList.contains("hidden"), "켜면 나타난다");
  app.run(`setKakaoWebtoonsEnabled(false);`);
  ok(select.classList.contains("hidden"), "끄면 사라진다");

  // Scenario 4: 선택이 나타난 뒤 카카오를 고르면 안내 문구(placeholder)도 카카오용으로 바뀐다
  app = makeApp({ ...base, "/api/settings/kakao-webtoons-enabled": { enabled: true } });
  select = app.w.document.getElementById("manual-platform");
  app.run(`switchToTab("manual-download");`); await sleep(60);
  select.value = "kakao"; select.dispatchEvent(new app.w.Event("change"));
  ok(/작품 번호/.test(app.w.document.getElementById("manual-query").placeholder), "카카오를 고르면 안내 문구가 카카오용");
  done();
})();
