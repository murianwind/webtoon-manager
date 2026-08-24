const tbody = document.getElementById("webtoon-tbody");
const emptyMessage = document.getElementById("empty-message");
const addError = document.getElementById("add-error");

let currentFilter = "all";
let allWebtoons = [];

const STATUS_LABEL = {
  active: "구독중",
  unsubscribed: "구독해제",
  excluded: "목록제외",
};

const SOURCE_LABEL = {
  manual: "수동",
  artist: "작가",
  tag: "태그",
};

async function apiCall(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

function render() {
  const filtered =
    currentFilter === "all"
      ? allWebtoons
      : allWebtoons.filter((w) => w.status === currentFilter);

  tbody.innerHTML = "";
  emptyMessage.classList.toggle("hidden", filtered.length > 0);

  for (const w of filtered) {
    const tr = document.createElement("tr");

    tr.innerHTML = `
      <td>${escapeHtml(w.title)}${w.is_adult ? " 🔞" : ""}</td>
      <td>${w.title_id}</td>
      <td><span class="badge ${w.status}">${STATUS_LABEL[w.status] || w.status}</span></td>
      <td>${w.last_downloaded_no}화</td>
      <td>${SOURCE_LABEL[w.added_source] || w.added_source}</td>
      <td>${w.is_finished ? '<span class="badge finished">완결</span>' : "-"}</td>
      <td class="row-actions"></td>
    `;

    const actionsCell = tr.querySelector(".row-actions");
    if (w.status !== "active") {
      actionsCell.appendChild(makeButton("구독", () => act(w.title_id, "subscribe")));
    }
    if (w.status !== "unsubscribed") {
      actionsCell.appendChild(makeButton("구독해제", () => act(w.title_id, "unsubscribe")));
    }
    if (w.status !== "excluded") {
      actionsCell.appendChild(makeButton("목록제외", () => act(w.title_id, "exclude")));
    }

    tbody.appendChild(tr);
  }
}

function makeButton(label, onClick) {
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function act(titleId, action) {
  try {
    await apiCall(`/api/webtoons/${titleId}/${action}`, { method: "POST" });
    await loadWebtoons();
  } catch (e) {
    alert(e.message);
  }
}

async function loadWebtoons() {
  allWebtoons = await apiCall("/api/webtoons");
  render();
}

document.querySelectorAll(".filter-tabs .tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".filter-tabs .tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentFilter = tab.dataset.status;
    render();
  });
});

document.getElementById("btn-add").addEventListener("click", async () => {
  addError.textContent = "";
  const titleId = document.getElementById("input-title-id").value.trim();
  const title = document.getElementById("input-title").value.trim();

  if (!titleId) {
    addError.textContent = "titleId를 입력해주세요.";
    return;
  }

  try {
    await apiCall("/api/webtoons", {
      method: "POST",
      body: JSON.stringify({ title_id: titleId, title: title || null }),
    });
    document.getElementById("input-title-id").value = "";
    document.getElementById("input-title").value = "";
    await loadWebtoons();
  } catch (e) {
    addError.textContent = e.message;
  }
});

document.getElementById("btn-import").addEventListener("click", async () => {
  const resultEl = document.getElementById("import-result");
  const text = document.getElementById("import-textarea").value;
  if (!text.trim()) return;
  try {
    const result = await apiCall("/api/import/id-list", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    resultEl.style.color = "";
    resultEl.textContent = `가져옴 ${result.imported.length}개, 건너뜀 ${result.skipped.length}개`;
    document.getElementById("import-textarea").value = "";
    await loadWebtoons();
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-scan-discovery").addEventListener("click", async () => {
  await apiCall("/api/scan/discovery", { method: "POST" });
  alert("신작 스캔을 백그라운드에서 시작했습니다. 잠시 후 새로고침해주세요.");
});

document.getElementById("btn-scan-download").addEventListener("click", async () => {
  await apiCall("/api/scan/download", { method: "POST" });
  alert("다운로드를 백그라운드에서 시작했습니다. 잠시 후 새로고침해주세요.");
});

loadWebtoons().catch((e) => alert(e.message));
