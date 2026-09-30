// 다운로드 이력(회차 단위)
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 다운로드 이력 (회차 단위) ─────────────────────────────

async function loadEpisodeHistory(page) {
  loadRetentionDays();
  const listContainer = document.getElementById("episode-history-list");
  const emptyEl = document.getElementById("episode-history-empty");
  const status = document.getElementById("episode-history-status").value;
  const search = document.getElementById("episode-history-search").value.trim();

  listContainer.innerHTML = "";
  try {
    const params = new URLSearchParams({ page: String(page) });
    if (status) params.set("status", status);
    if (search) params.set("search", search);
    const data = await apiCall(`/api/episode-history?${params.toString()}`);

    emptyEl.classList.toggle("hidden", data.items.length > 0);
    for (const item of data.items) {
      const wrap = document.createElement("div");
      wrap.className = "job-history-entry";
      const timeLabel = formatKoreanTime(item.downloaded_at);
      const statusLabel = item.status === "success" ? "성공" : `실패${item.error_msg ? ` (${item.error_msg})` : ""}`;

      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      summary.innerHTML = `
        <span class="job-history-name">${escapeHtml(item.title_name)}</span>
        <span class="job-history-time">${item.episode_no}화 ${escapeHtml(item.subtitle)} · ${escapeHtml(timeLabel)}</span>
        <span class="badge job-${item.status}">${escapeHtml(statusLabel)}</span>
      `;
      const deleteBtn = makeButton("삭제", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/episode-history/${item.id}`, { method: "DELETE" });
        loadEpisodeHistory(page);
      });
      deleteBtn.className = "job-history-delete-btn";
      summary.appendChild(deleteBtn);
      wrap.appendChild(summary);
      listContainer.appendChild(wrap);
    }
    renderEpisodeHistoryPagination(data.page, data.total, data.page_size);
  } catch (e) {
    emptyEl.textContent = e.message;
    emptyEl.classList.remove("hidden");
  }
}

function renderEpisodeHistoryPagination(page, total, pageSize) {
  const container = document.getElementById("episode-history-pagination");
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  container.innerHTML = "";
  if (totalPages <= 1) return;

  if (page > 1) container.appendChild(makeButton("이전", () => loadEpisodeHistory(page - 1)));
  const label = document.createElement("span");
  label.textContent = ` ${page} / ${totalPages} `;
  container.appendChild(label);
  if (page < totalPages) container.appendChild(makeButton("다음", () => loadEpisodeHistory(page + 1)));
}

document.getElementById("episode-history-search").addEventListener("input", () => loadEpisodeHistory(1));
document.getElementById("episode-history-status").addEventListener("change", () => loadEpisodeHistory(1));

document.getElementById("btn-clear-episode-history").addEventListener("click", async () => {
  if (!confirm("다운로드 이력을 전부 지웁니다 (받은 파일은 그대로 유지됩니다). 계속할까요?")) return;
  await apiCall("/api/episode-history", { method: "DELETE" });
  loadEpisodeHistory(1);
});

async function loadRetentionDays() {
  try {
    const data = await apiCall("/api/episode-history/retention-days");
    document.getElementById("episode-history-retention-days").value = data.retention_days || "";
  } catch (e) {
    // 조용히 무시 — 핵심 목록 표시에 지장 없어야 함
  }
}

document.getElementById("btn-save-retention-days").addEventListener("click", async () => {
  const resultEl = document.getElementById("retention-save-result");
  resultEl.textContent = "";
  const input = document.getElementById("episode-history-retention-days");
  const days = Number(input.value) || 0;
  try {
    await apiCall("/api/episode-history/retention-days", {
      method: "POST",
      body: JSON.stringify({ retention_days: days }),
    });
    resultEl.style.color = "";
    resultEl.textContent = days > 0 ? "저장했습니다." : "자동삭제 껐습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});
