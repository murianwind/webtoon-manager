// 수동 다운로드(네이버 + 카카오페이지 분석/구독/받기)
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 수동 다운로드 ────────────────────────────────────────

let manualAnalyzeResult = null;
let manualPollTimer = null;

document.getElementById("manual-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("btn-manual-analyze").click();
  }
});

// ── 수동 다운로드: 카카오페이지 ───────────────────────────
// 작품을 폴더 규칙으로 분석해서 회차 표(번호/제목/상태/대여 만료/진행)를 보여주고, 고른 회차를 받는다. 이미
// 받은 회차는 표시만 되고, 직접 고르면 다시 받아 교체할 수 있다(자동 다운로드는 안 받은 회차만 받는다).

let kakaoManualAnalysis = null;
let kakaoManualJobRunning = false;

function currentManualPlatform() {
  return kakaoWebtoonsEnabled ? document.getElementById("manual-platform").value : "naver";
}

function applyManualPlatformView() {
  const isKakao = currentManualPlatform() === "kakao";
  document.getElementById("kakao-manual-result").classList.toggle("hidden", !isKakao || !kakaoManualAnalysis);
  document.getElementById("manual-result").classList.toggle("hidden", isKakao || !manualAnalyzeResult);
  document.getElementById("manual-search-results").classList.add("hidden");
  document.getElementById("manual-query").placeholder = isKakao
    ? "작품 번호, 작품 주소(page.kakao.com/content/…) 또는 제목"
    : "titleId 또는 웹툰 제목";
}

function syncManualPlatformSelect() {
  document.getElementById("manual-platform").classList.toggle("hidden", !kakaoWebtoonsEnabled);
  applyManualPlatformView();
}

document.getElementById("manual-platform").addEventListener("change", applyManualPlatformView);

function parseKakaoSeriesId(query) {
  if (/^\d+$/.test(query)) return Number(query);
  const m = query.match(/page\.kakao\.com\/(?:[a-z]{2}\/)?content\/(\d+)/i);
  return m ? Number(m[1]) : null;
}

async function kakaoManualStart(query) {
  const resultsEl = document.getElementById("manual-search-results");
  const seriesId = parseKakaoSeriesId(query);
  if (seriesId) {
    resultsEl.classList.add("hidden");
    await runKakaoManualAnalyze(seriesId);
    return;
  }
  const matches = await apiCall(`/api/kakao-manual/search?query=${encodeURIComponent(query)}`);
  resultsEl.innerHTML = "";
  if (matches.length === 0) {
    resultsEl.innerHTML = "<p>일치하는 웹툰이 없습니다.</p>";
    resultsEl.classList.remove("hidden");
    return;
  }
  for (const m of matches) {
    const card = document.createElement("div");
    card.className = "webtoon-card";
    card.innerHTML = `
      ${m.thumbnail_url ? `<img src="${escapeHtml(m.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '<div class="thumb-placeholder"></div>'}
      <div class="webtoon-card-body">
        <div class="webtoon-card-title">${escapeHtml(m.title)}</div>
        <div class="webtoon-card-meta">${escapeHtml([m.authors, m.status].filter(Boolean).join(" · "))}</div>
      </div>
      <div class="webtoon-card-actions"></div>
    `;
    const cardActions = card.querySelector(".webtoon-card-actions");
    cardActions.appendChild(makeButton("이 작품 분석", () => runKakaoManualAnalyze(m.title_id)));
    const alreadySubscribed = m.subscription === "active";
    const subscribeBtn = makeButton(alreadySubscribed ? "구독 중" : "구독", async () => {
      try {
        await apiCall(`/api/kakao-webtoons/${m.title_id}/subscribe`, {
          method: "POST",
          body: JSON.stringify({ title: m.title, thumbnail_url: m.thumbnail_url || "", author_summary: m.authors || "" }),
        });
        subscribeBtn.textContent = "구독 중";
        subscribeBtn.disabled = true;
      } catch (e) {
        alert(e.message);
      }
    });
    subscribeBtn.disabled = alreadySubscribed; // 이미 구독 중이면 누를 수 없다(해제는 전체목록에서)
    cardActions.appendChild(subscribeBtn);
    resultsEl.appendChild(card);
  }
  resultsEl.classList.remove("hidden");
}

async function runKakaoManualAnalyze(seriesId) {
  try {
    kakaoManualAnalysis = await apiCall(`/api/kakao-manual/analyze?series_id=${encodeURIComponent(seriesId)}`);
    document.getElementById("manual-search-results").classList.add("hidden");
    renderKakaoManualTable();
  } catch (e) {
    alert(e.message);
  }
}

const KAKAO_STATE_BADGE = { free: ["kp-free", "무료"], owned: ["kp-owned", "보유"], waitfree: ["kp-waitfree", "기다무"], locked: ["kp-locked", "잠금"] };

// 기다무를 다시 쓸 수 있기까지 남은 시간(카카오페이지 작품 화면의 "23시간 59분 남음"과 같은 표시)
function formatRemaining(isoString) {
  const ms = new Date(isoString).getTime() - Date.now();
  if (!isFinite(ms) || ms <= 0) return "";
  const minutes = Math.ceil(ms / 60000);
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분 남음`;
}

// 기다무는 작품마다 충전 주기가 다르다(3시간/1일/3일 등) — 이용권 응답의 실제 주기로 표시한다
function formatWaitfreePeriod(minutes) {
  if (!minutes) return "";
  if (minutes % 1440 === 0) return `${minutes / 1440}일`;
  if (minutes % 60 === 0) return `${minutes / 60}시간`;
  return `${minutes}분`;
}

function kakaoTicketsHtml(t) {
  if (!t) return "";
  if (t.waitfree_supported === false) return `<div class="kakao-manual-tickets"><span>대여권 <b>${t.rental_count}</b>장</span></div>`; // 기다무가 없는 작품(연재무료 등)은 기다무 항목을 숨긴다
  const waitfree = t.waitfree_ready
    ? "<b>지금 사용 가능</b>"
    : `사용 중${t.waitfree_available_at && formatRemaining(t.waitfree_available_at) ? ` (${formatRemaining(t.waitfree_available_at)})` : ""}`;
  return `<div class="kakao-manual-tickets"><span>대여권 <b>${t.rental_count}</b>장</span><span>${formatWaitfreePeriod(t.waitfree_period_minutes)} 기다무 대여권 ${waitfree}</span></div>`;
}

function refreshKakaoManualSubscribeButton() {
  // 구독만 있고 구독해제는 없다 — 구독 중이면 버튼 대신 "구독 중"만 보여준다(해제는 전체목록에서)
  const a = kakaoManualAnalysis;
  const btn = document.getElementById("btn-kakao-manual-subscribe");
  btn.classList.toggle("hidden", !a);
  if (!a) return;
  const subscribed = a.subscription === "active";
  btn.textContent = subscribed ? "구독 중" : "구독";
  btn.disabled = subscribed;
}

document.getElementById("btn-kakao-manual-subscribe").addEventListener("click", async () => {
  const a = kakaoManualAnalysis;
  if (!a || a.subscription === "active") return;
  try {
    // 완결작이라 목록에 없는 작품도 여기서 구독할 수 있고, 구독하면 전체목록에 나타나 자동 다운로드 대상이 된다
    await apiCall(`/api/kakao-webtoons/${a.series_id}/subscribe`, {
      method: "POST",
      body: JSON.stringify({ title: a.title, thumbnail_url: a.thumbnail_url || "", author_summary: a.authors || "" }),
    });
    a.subscription = "active";
    refreshKakaoManualSubscribeButton();
  } catch (e) {
    alert(e.message);
  }
});

function kakaoManualSummaryHtml(a) {
  const lines = [`저장 폴더: ${escapeHtml(a.folder)}`];
  if (a.mode === "new_folder") {
    lines.push("이 작품 폴더에 받은 파일이 없어서 처음(프롤로그 포함)부터 받습니다.");
  } else if (a.mode === "single_marker" && a.marker) {
    lines.push(`폴더에 파일이 하나(${a.marker.number}번 ${escapeHtml(a.marker.subtitle)})뿐이라 그 회차 이후부터 받습니다.`);
  } else {
    lines.push(`폴더에 받은 회차가 ${a.existing_count}개 있어서, 가장 이른 파일 이후에 빠진 회차만 받습니다.`);
  }
  // warning은 true/false(불리언)다 — 값을 그대로 찍으면 "true"가 보이므로 사람이 읽는 안내문을 만든다
  if (a.marker && a.marker.warning) {
    lines.push(`<span class="warn">⚠ 폴더의 파일(${a.marker.number}번 ${escapeHtml(a.marker.subtitle)})이 사이트 회차와 맞지 않아 번호대로 이어받습니다. 폴더의 파일 이름을 확인해 주세요.</span>`);
  }
  else if (a.marker && a.marker.resolved_number !== a.marker.number) {
    lines.push(`파일의 번호(${a.marker.number})가 사이트와 달라서 제목으로 찾은 ${a.marker.resolved_number}번 이후부터 받습니다.`);
  }
  const counts = [`이미 받음 ${a.downloaded_count}개`, `받을 회차 ${a.to_download_count}개`, `잠겨서 대기 ${a.locked_count}개`];
  if (a.before_start_count > 0) counts.push(`시작 지점 이전 ${a.before_start_count}개`);
  lines.push(counts.join(" · "));
  // 사이트가 말하는 전체 회차 수(동영상으로 뺀 것 포함)보다 가져온 회차가 적으면, 목록에서 회차가 빠졌을 수 있다고 알려 준다
  const fetched = a.listed_count + a.excluded_video_count;
  if (a.site_total > 0 && fetched < a.site_total) {
    const hidden = a.hidden_count > 0 ? ` (그중 숨김 처리된 회차 ${a.hidden_count}개는 표에서 뺐습니다)` : "";
    lines.push(`<span class="warn">⚠ 사이트 회차는 ${a.site_total}개인데 ${a.listed_count}개만 가져왔습니다${hidden}. 빠진 회차가 있을 수 있습니다.</span>`);
  }
  if (!a.cookie_saved) lines.push('<span class="warn">⚠ 로그인 쿠키가 없습니다(설정에서 저장). 지금은 무료 회차만 받을 수 있습니다.</span>');
  else if (a.logged_in === false) lines.push('<span class="warn">⚠ 로그인이 풀려 있습니다. 설정에서 쿠키를 다시 저장해주세요.</span>');
  else if (a.logged_in === null) lines.push('<span class="warn">로그인 상태를 확인하지 못했습니다(잠시 뒤 다시 분석해보세요).</span>');
  return lines.join("<br>");
}

function renderKakaoManualTable() {
  const a = kakaoManualAnalysis;
  document.getElementById("manual-result").classList.add("hidden");
  document.getElementById("kakao-manual-result").classList.remove("hidden");
  document.getElementById("kakao-manual-title").textContent = a.title;
  document.getElementById("kakao-manual-summary").innerHTML = kakaoManualSummaryHtml(a) + kakaoTicketsHtml(a.tickets);
  refreshKakaoManualSubscribeButton();
  const tbody = document.getElementById("kakao-manual-tbody");
  tbody.innerHTML = "";
  // "미보유만 선택"은 이미 받았거나 시작 지점 이전인 회차가 있어서 "전체선택"과 결과가 다를 때만 보여준다
  const missingDiffers = a.episodes.some((e) => e.selectable && (e.downloaded || e.before_start));
  document.getElementById("btn-kakao-manual-select-missing").classList.toggle("hidden", !missingDiffers);
  for (const e of a.episodes) {
    const [badgeClass, badgeLabel] = KAKAO_STATE_BADGE[e.state] || ["kp-locked", e.state];
    const progress = e.downloaded
      ? '<span class="kp-badge kp-done">이미받음</span>'
      : e.before_start
        ? '<span class="kp-before">이전 회차</span>'
        : '<span class="kp-wait">대기</span>';
    // 대여 중이면 만료 시각, 잠긴 연재무료 회차면 무료가 되는 날짜
    const expire = e.expire ? String(e.expire).replace("T", " ").slice(0, 16) : e.free_at ? `${e.free_at} 무료` : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="kakao-manual-ep-checkbox" data-no="${e.number}" ${e.selectable ? "" : "disabled"} /></td>
      <td>${e.number}</td>
      <td>${escapeHtml(e.subtitle)}</td>
      <td><span class="kp-badge ${badgeClass}">${badgeLabel}</span></td>
      <td>${escapeHtml(expire)}</td>
      <td>${progress}</td>`;
    tr.querySelector("input").dataset.downloaded = e.downloaded ? "1" : "";
    tr.querySelector("input").dataset.waitfree = e.state === "waitfree" ? "1" : "";
    tr.querySelector("input").dataset.missing = e.selectable && !e.downloaded && !e.before_start ? "1" : "";
    tbody.appendChild(tr);
  }
  fitScrollWrapperToViewport("kakao-manual-table-wrapper", 240);
}

function kakaoManualCheckboxes() {
  return Array.from(document.querySelectorAll(".kakao-manual-ep-checkbox:not(:disabled)"));
}

// 고른 회차 중 기다무가 필요한 회차는 사용할 수 있는 장수(작품당 한 장)만큼만 고른다 — 번호가 가장 앞선 것부터. 나머지는 열 수 없어서
// 골라 봐야 "건너뜀"이 되기 때문이다. 이미 받은 회차를 다시 받으려고 기다무를 쓰는 일은 이 버튼들이 하지 않는다(직접 체크하면 가능).
function selectKakaoManual(wanted) {
  let waitfreeLeft = 1;
  for (const cb of kakaoManualCheckboxes().sort((x, y) => Number(x.dataset.no) - Number(y.dataset.no))) {
    let on = wanted(cb);
    if (on && cb.dataset.waitfree === "1") {
      on = waitfreeLeft > 0 && cb.dataset.downloaded !== "1";
      if (on) waitfreeLeft -= 1;
    }
    cb.checked = on;
  }
}
document.getElementById("btn-kakao-manual-select-all").addEventListener("click", () => selectKakaoManual(() => true));
document.getElementById("btn-kakao-manual-select-missing").addEventListener("click", () => selectKakaoManual((cb) => cb.dataset.missing === "1"));
document.getElementById("btn-kakao-manual-select-none").addEventListener("click", () => {
  kakaoManualCheckboxes().forEach((cb) => (cb.checked = false));
});
document.getElementById("btn-kakao-manual-download").addEventListener("click", async () => {
  const checked = Array.from(document.querySelectorAll(".kakao-manual-ep-checkbox:checked"));
  if (checked.length === 0) {
    alert("다운로드할 회차를 선택해주세요.");
    return;
  }
  const already = checked.filter((cb) => cb.dataset.downloaded === "1").length;
  if (already > 0 && !confirm(`이미 받은 회차가 ${already}개 포함되어 있습니다. 다시 받아서 기존 파일을 교체할까요?`)) return;
  // 기다무 상태인 회차는 기다무 대여권을 쓴다 — 한 장뿐이라 고른 것 중 번호가 가장 앞선 하나만 열린다
  const waitfreeNos = checked.filter((cb) => cb.dataset.waitfree === "1").map((cb) => Number(cb.dataset.no)).sort((x, y) => x - y);
  if (waitfreeNos.length > 0 && !confirm(`${waitfreeNos[0]}번 회차를 열려고 기다무 대여권 1장을 사용합니다.${waitfreeNos.length > 1 ? ` (기다무는 한 장이라 나머지 ${waitfreeNos.length - 1}개는 열리지 않습니다)` : ""}\n계속할까요?`)) return;
  try {
    await apiCall("/api/kakao-manual/run", {
      method: "POST",
      body: JSON.stringify({ series_id: kakaoManualAnalysis.series_id, numbers: checked.map((cb) => Number(cb.dataset.no)) }),
    });
    kakaoManualJobRunning = true;
    startManualPolling();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-manual-analyze").addEventListener("click", async () => {
  const query = document.getElementById("manual-query").value.trim();
  if (!query) return;
  if (currentManualPlatform() === "kakao") {
    try {
      await kakaoManualStart(query);
    } catch (e) {
      alert(e.message);
    }
    return;
  }

  const resultsEl = document.getElementById("manual-search-results");

  if (/^\d+$/.test(query)) {
    // 숫자만 입력 -> titleId로 바로 분석
    resultsEl.classList.add("hidden");
    await runManualAnalyze(query);
    return;
  }

  // 텍스트 입력 -> 제목 검색 후보 표시
  try {
    const matches = await apiCall(`/api/manual-download/search?query=${encodeURIComponent(query)}`);
    resultsEl.innerHTML = "";
    if (matches.length === 0) {
      resultsEl.innerHTML = "<p>일치하는 웹툰이 없습니다.</p>";
      resultsEl.classList.remove("hidden");
      return;
    }
    for (const m of matches) {
      const card = document.createElement("div");
      card.className = "webtoon-card";
      card.innerHTML = `
        ${m.thumbnail_url ? `<img src="${escapeHtml(m.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>'}
        <div class="webtoon-card-body"><div class="webtoon-card-title">${escapeHtml(m.title)}</div></div>
        <div class="webtoon-card-actions"></div>
      `;
      card.querySelector(".webtoon-card-actions").appendChild(
        makeButton("이 작품 분석", () => runManualAnalyze(m.title_id))
      );
      resultsEl.appendChild(card);
    }
    resultsEl.classList.remove("hidden");
  } catch (e) {
    alert(e.message);
  }
});

async function runManualAnalyze(titleId) {
  try {
    manualAnalyzeResult = await apiCall(`/api/manual-download/analyze?title_id=${encodeURIComponent(titleId)}`);
    document.getElementById("manual-search-results").classList.add("hidden");
    renderManualTable();
  } catch (e) {
    alert(e.message);
  }
}

function renderManualTable() {
  document.getElementById("manual-result").classList.remove("hidden");
  document.getElementById("manual-title-name").textContent = manualAnalyzeResult.title;

  const tbody = document.getElementById("manual-tbody");
  tbody.innerHTML = "";
  for (const ep of manualAnalyzeResult.episodes) {
    const tr = document.createElement("tr");
    const statusLabel = ep.is_locked ? "유료/잠김" : ep.owned ? "보유함" : "미보유";
    tr.innerHTML = `
      <td><input type="checkbox" class="manual-ep-checkbox" data-no="${ep.episode_no}" ${ep.is_locked ? "disabled" : ""} /></td>
      <td>${ep.episode_no}</td>
      <td>${escapeHtml(ep.subtitle)}</td>
      <td>${statusLabel}</td>
    `;
    tbody.appendChild(tr);
  }
  // 검색결과 영역이 숨겨지면서 레이아웃이 바뀐 다음(다음 페인트 이후) 계산해야
  // 정확한 남은 높이가 나온다.
  requestAnimationFrame(() => fitScrollWrapperToViewport("manual-table-wrapper", 240));
}

document.getElementById("btn-manual-select-all").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox:not(:disabled)").forEach((cb) => (cb.checked = true));
});
document.getElementById("btn-manual-select-none").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox").forEach((cb) => (cb.checked = false));
});
document.getElementById("btn-manual-select-missing").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox").forEach((cb) => {
    const no = Number(cb.dataset.no);
    const ep = manualAnalyzeResult.episodes.find((e) => e.episode_no === no);
    cb.checked = !cb.disabled && ep && !ep.owned;
  });
});

document.getElementById("btn-manual-download").addEventListener("click", async () => {
  const episodeNos = Array.from(document.querySelectorAll(".manual-ep-checkbox:checked")).map((cb) => Number(cb.dataset.no));
  if (episodeNos.length === 0) {
    alert("다운로드할 회차를 선택해주세요.");
    return;
  }
  try {
    await apiCall("/api/manual-download/run", {
      method: "POST",
      body: JSON.stringify({ title_id: manualAnalyzeResult.title_id, episode_nos: episodeNos }),
    });
    startManualPolling();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-copy-manual-log").addEventListener("click", () => {
  copyTextToClipboard(document.getElementById("manual-log").innerText);
});

// navigator.clipboard는 HTTPS/localhost가 아니면(예: http://192.168.x.x:8001로 접속하는
// LAN 환경) 브라우저에서 자체적으로 비활성화되어 있어서 조용히 아무 일도 안 일어난다
// (실제로 이것 때문에 "로그 복사가 안 된다"는 문제가 있었다) — 안 되면 구식 방식으로 대체한다.
function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand("copy");
  } catch (e) {
    alert("클립보드 복사에 실패했습니다.");
  }
  document.body.removeChild(textarea);
}

function startManualPolling() {
  stopManualPolling();
  refreshManualStatus();
  manualPollTimer = setInterval(refreshManualStatus, 2000);
}
function stopManualPolling() {
  if (manualPollTimer) {
    clearInterval(manualPollTimer);
    manualPollTimer = null;
  }
}
async function refreshManualStatus() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  const st = statuses.manual;
  if (!st) return;
  const badge = document.getElementById("manual-status-badge");
  badge.textContent = st.status;
  badge.className = `badge job-${st.status}`;
  renderJobLog("manual", st.log);
  if (st.status !== "running") {
    stopManualPolling();
    if (kakaoManualJobRunning) {
      kakaoManualJobRunning = false;
      if (kakaoManualAnalysis) runKakaoManualAnalyze(kakaoManualAnalysis.series_id); // 받은 결과를 표에 반영
    }
  }
}
