// 설정: 디스코드, 백업/복원
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 설정: 디스코드 ───────────────────────────────────────

const BOT_TOKEN_MASK = "••••••••••••••••";
const WEBHOOK_URL_MASK = "••••••••••••••••••••••••••••";

async function loadDiscordSettings() {
  try {
    const s = await apiCall("/api/settings/discord");

    const webhookInput = document.getElementById("discord-webhook-url");
    webhookInput.value = s.webhook_url_set ? WEBHOOK_URL_MASK : "";
    webhookInput.dataset.masked = s.webhook_url_set ? "true" : "false";

    document.getElementById("discord-channel-id").value = s.notify_channel_id;

    const tokenInput = document.getElementById("discord-bot-token");
    tokenInput.value = s.bot_token_set ? BOT_TOKEN_MASK : "";
    tokenInput.dataset.masked = s.bot_token_set ? "true" : "false";

    document.getElementById("discord-bot-status").textContent = s.bot_ready ? "🟢 봇 연결됨" : "⚪ 봇 연결 안 됨";
    updateSettingsCardVisibility();
  } catch (e) {
    document.getElementById("discord-save-result").textContent = e.message;
  }
}

// "디스코드 설정"(웹훅) -> "실행 스케줄"/"다운로드 리포트" -> (그 안의 "미등록 웹툰
// 중 새 에피소드" 토글) -> "카카오웹툰 관리" 순으로 하나씩 조건이 채워져야 다음
// 카드가 나타난다 — 신작 알림/리포트를 보낼 데가 없으면 스케줄이나 리포트 세부
// 설정 자체가 의미 없고, 카카오는 그 리포트의 한 항목(미등록 새 에피소드)에
// 얹혀서 나가는 기능이라 그게 꺼져 있으면 역시 의미가 없어서 이 순서로 숨겨둔다.
// 기존 저장된 설정 값 자체는 전혀 안 건드리고 화면에 보일지만 정할 뿐이라, 다시
// 조건을 채우면 이미 저장해뒀던 값 그대로 나타난다.
function updateSettingsCardVisibility() {
  // "실행 스케줄"은 신작 스캔/다운로드/아카이빙처럼 디스코드와 무관한 것도 다루니
  // 웹훅 여부와 상관없이 항상 보인다. "다운로드 리포트"(발송 시각 포함)만 보낼 곳이
  // 있어야 의미가 있어서 웹훅이 설정돼야 나타난다.
  const webhookConfigured = document.getElementById("discord-webhook-url").dataset.masked === "true";
  document.getElementById("settings-card-report").classList.toggle("hidden", !webhookConfigured);

  const unregisteredEnabled = document.getElementById("report-unregistered-toggle").checked;
  document.getElementById("settings-card-kakao").classList.toggle("hidden", !(webhookConfigured && unregisteredEnabled));
}

document.getElementById("discord-webhook-url").addEventListener("focus", (e) => {
  if (e.target.dataset.masked === "true") {
    e.target.value = "";
    e.target.dataset.masked = "false";
  }
});

document.getElementById("discord-bot-token").addEventListener("focus", (e) => {
  if (e.target.dataset.masked === "true") {
    e.target.value = "";
    e.target.dataset.masked = "false";
  }
});

document.getElementById("btn-save-discord").addEventListener("click", async () => {
  const resultEl = document.getElementById("discord-save-result");
  resultEl.textContent = "";
  try {
    const tokenInput = document.getElementById("discord-bot-token");
    const rawToken = tokenInput.value.trim();
    const tokenToSend = rawToken === BOT_TOKEN_MASK ? "" : rawToken;

    const webhookInput = document.getElementById("discord-webhook-url");
    const rawWebhook = webhookInput.value.trim();
    const webhookToSend = rawWebhook === WEBHOOK_URL_MASK ? "" : rawWebhook;

    await apiCall("/api/settings/discord", {
      method: "POST",
      body: JSON.stringify({
        webhook_url: webhookToSend,
        bot_token: tokenToSend,
        notify_channel_id: document.getElementById("discord-channel-id").value.trim(),
      }),
    });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다. 봇을 재연결하는 중입니다 (몇 초 걸릴 수 있음).";
    // 빈 문자열로 저장해도 기존 웹훅은 안 지워지므로(discord_config.set_webhook_url
    // 참고), "이미 마스킹돼 있었다" OR "방금 뭔가 입력했다" 둘 중 하나면 바로 설정된
    // 것으로 보고 3초씩 기다리지 않고 즉시 다음 카드들을 보여준다.
    if (webhookInput.dataset.masked === "true" || webhookToSend !== "") {
      webhookInput.dataset.masked = "true";
      updateSettingsCardVisibility();
    }
    setTimeout(loadDiscordSettings, 3000);
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-test-webhook").addEventListener("click", async () => {
  const result = await apiCall("/api/settings/discord/test-webhook", { method: "POST" });
  alert(result.message);
});

document.getElementById("btn-test-bot").addEventListener("click", async () => {
  const result = await apiCall("/api/settings/discord/test-bot", { method: "POST" });
  alert(result.message);
});

// ── 설정: 백업/복원 ──────────────────────────────────────

document.getElementById("btn-backup-download").addEventListener("click", async () => {
  try {
    const backup = await apiCall("/api/backup");
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `webtoon-manager-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    document.getElementById("backup-result").textContent = e.message;
  }
});

document.getElementById("restore-file-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById("backup-result");
  resultEl.textContent = "";
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!confirm("현재 데이터를 모두 지우고 이 백업으로 복원합니다. 계속할까요?")) {
      event.target.value = "";
      return;
    }
    await apiCall("/api/restore", { method: "POST", body: JSON.stringify(data) });
    resultEl.style.color = "";
    resultEl.textContent = "복원 완료. 페이지를 새로고침해주세요.";
  } catch (e) {
    resultEl.textContent = `복원 실패: ${e.message}`;
  }
  event.target.value = "";
});
