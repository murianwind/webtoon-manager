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

// 설정 카드 중 조건이 있는 것은 "다운로드 리포트" 하나뿐이다 — 보낼 곳(디스코드 웹훅)이 있어야
// 의미가 있어서 웹훅이 저장돼야 나타난다. "카카오웹툰 관리"는 예전엔 리포트의 한 항목(미등록 새
// 에피소드)에 얹힌 기능이라 웹훅과 그 옵션 뒤에 숨겼지만, 이제 카카오페이지 다운로드(로그인 쿠키
// 입력 포함)를 하는 곳이라 항상 보인다. 웹훅이 없으면 그 카드에 "알림은 못 받는다"는 안내만 띄운다.
// 화면에 보일지만 정할 뿐, 저장된 설정 값은 건드리지 않는다.
function updateSettingsCardVisibility() {
  // "실행 스케줄"은 신작 스캔/다운로드/아카이빙처럼 디스코드와 무관한 것도 다루니
  // 웹훅 여부와 상관없이 항상 보인다. "다운로드 리포트"(발송 시각 포함)만 보낼 곳이
  // 있어야 의미가 있어서 웹훅이 설정돼야 나타난다.
  const webhookConfigured = document.getElementById("discord-webhook-url").dataset.masked === "true";
  document.getElementById("settings-card-report").classList.toggle("hidden", !webhookConfigured);

  document.getElementById("kakao-no-webhook-hint").classList.toggle("hidden", webhookConfigured);
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

// 복원이 끝난 뒤 화면을 새로고침한다(테스트에서 바꿔 끼울 수 있게 함수로 둔다).
function scheduleReload() {
  setTimeout(() => location.reload(), 1500);
}

// 복원 결과를 보여 준다. 확인할 것(warnings)이나 다시 입력할 것(reenter)이 있으면 목록으로 보여 주고 직접 새로고침하게 두고(읽을 시간),
// 없으면 잠시 뒤 자동으로 새로고침한다.
function renderRestoreReport(report) {
  const box = document.getElementById("backup-report");
  const resultEl = document.getElementById("backup-result");
  const warnings = report.warnings || [];
  const reenter = report.reenter || [];
  resultEl.style.color = "";
  box.innerHTML = "";
  if (warnings.length === 0 && reenter.length === 0) {
    box.classList.add("hidden");
    resultEl.textContent = "복원 완료. 잠시 뒤 자동으로 새로고침합니다.";
    scheduleReload();
    return;
  }
  resultEl.textContent = "복원 완료. 아래 안내를 확인해 주세요.";
  const addList = (title, cssClass, lines) => {
    if (lines.length === 0) return;
    const heading = document.createElement("strong");
    heading.textContent = title;
    const list = document.createElement("ul");
    list.className = cssClass;
    for (const line of lines) {
      const li = document.createElement("li");
      li.textContent = line;
      list.appendChild(li);
    }
    box.append(heading, list);
  };
  addList("⚠ 확인해 주세요", "backup-warnings", warnings);
  addList("🔑 다시 입력해 주세요(백업에 들어 있지 않습니다)", "backup-reenter", reenter);
  const reloadButton = document.createElement("button");
  reloadButton.textContent = "확인했습니다 — 새로고침";
  reloadButton.addEventListener("click", () => location.reload());
  box.appendChild(reloadButton);
  box.classList.remove("hidden");
}

document.getElementById("restore-file-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById("backup-result");
  resultEl.textContent = "";
  document.getElementById("backup-report").classList.add("hidden");
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!confirm("현재 데이터를 모두 지우고 이 백업으로 복원합니다. 디스코드 웹훅/봇 토큰과 카카오페이지 로그인 정보는 지금 설정된 그대로 유지됩니다. 계속할까요?")) {
      event.target.value = "";
      return;
    }
    const report = await apiCall("/api/restore", { method: "POST", body: JSON.stringify(data) });
    renderRestoreReport(report);
  } catch (e) {
    resultEl.textContent = `복원 실패: ${e.message}`;
  }
  event.target.value = "";
});
