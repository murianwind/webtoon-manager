// 설정 화면 카드 — 순서(다운로드 폴더 → 카카오웹툰 관리 → 실행 스케줄 → 디스코드 → 다운로드 리포트)와 표시 조건.
// 카카오웹툰 관리는 이제 디스코드/리포트 옵션과 무관하게 항상 보인다(쿠키를 넣어야 다운로드를 쓸 수 있으므로). 리포트 카드만 웹훅 뒤에 나타난다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const state = { webhook: false, unregistered: false };
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/settings/discord": () => ({ webhook_url_set: state.webhook, bot_token_set: false, notify_channel_id: "", bot_ready: false }),
    "/api/settings/report-unregistered-new-episodes": (o) => { if (o.method === "POST") state.unregistered = JSON.parse(o.body).enabled; return { enabled: state.unregistered }; },
  };
  const { w, run } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  const shown = (id) => !$(id).classList.contains("hidden");

  // 1) 순서
  const ids = Array.from(w.document.querySelectorAll("#page-settings .settings-card[id]")).map((e) => e.id);
  const want = ["settings-card-download-roots", "settings-card-kakao", "settings-card-schedule", "settings-card-discord", "settings-card-report"];
  ok(JSON.stringify(ids.filter((i) => want.includes(i))) === JSON.stringify(want), "카드 순서: 다운로드 폴더 → 카카오웹툰 관리 → 실행 스케줄 → 디스코드 → 다운로드 리포트: " + JSON.stringify(ids));
  const all = Array.from(w.document.querySelectorAll("#page-settings > .settings-card, #page-settings .settings-card")).map((e) => e.id || e.querySelector("h2,h3")?.textContent.trim());
  ok(all.indexOf("settings-card-report") < all.findIndex((t) => /작가 자동 등록/.test(t || "")), "작가 자동 등록/구독해제 관리/백업 복원은 그 아래에 그대로");

  // 2) 웹훅 없음 + 리포트 옵션 꺼짐: 카카오 카드는 보이고(안내 표시), 리포트 카드는 숨김
  run("window.__p = loadSettingsPage();"); await w.__p; await sleep(60);
  ok(shown("settings-card-kakao") && !shown("settings-card-report"), "웹훅이 없어도 카카오웹툰 관리는 보이고, 다운로드 리포트는 숨겨짐");
  ok(shown("kakao-no-webhook-hint") && /알림/.test($("kakao-no-webhook-hint").textContent) && /다운로드는 그대로/.test($("kakao-no-webhook-hint").textContent), "웹훅이 없으면 '알림은 못 받지만 다운로드는 그대로'라는 안내가 보임");
  ok(shown("kp-cookie-json") || $("kp-cookie-json") !== null, "쿠키 입력칸이 카카오 카드 안에 있어 웹훅 없이도 입력 가능");

  // 3) 웹훅 저장: 리포트 카드가 나타나고 안내는 사라짐. 리포트 옵션을 꺼도 카카오 카드는 그대로
  state.webhook = true; run("window.__p = loadDiscordSettings();"); await w.__p; await sleep(30);
  ok(shown("settings-card-report") && shown("settings-card-kakao") && !shown("kakao-no-webhook-hint"), "웹훅이 있으면 리포트 카드가 나타나고 안내는 숨겨짐");
  $("report-unregistered-toggle").checked = true; $("report-unregistered-toggle").dispatchEvent(new w.Event("change")); await sleep(30);
  $("report-unregistered-toggle").checked = false; $("report-unregistered-toggle").dispatchEvent(new w.Event("change")); await sleep(30);
  ok(state.unregistered === false && shown("settings-card-kakao"), "리포트의 '미등록 웹툰 새 에피소드' 옵션을 꺼도 카카오 카드는 사라지지 않음(옵션은 리포트 내용만 정함)");
  done();
})();
