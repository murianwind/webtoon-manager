// "기타" 탭 원인 안내 — 기타를 볼 때 그 작품들이 왜 요일을 모르는지(완결/서비스 종료/휴재/확인 대기/확인 실패 …)를 원인별 개수로 보여 준다.
// 서버(/api/diagnostics/weekday-gaps)가 센 결과를 그대로 보여 주고, 기타가 아닌 탭에서는 요청도 안 하고 숨긴다. 목록을 다시 불러오면 새로 센다.
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)

const ETC_REASON_LABELS = {
  naver: { finished: "완결", no_weekday_on_page: "작품 페이지에 요일 없음", check_failed: "확인 실패", pending: "요일 확인 대기" },
  kakao: { finished: "완결", ended: "서비스 종료", hiatus: "휴재", serving_not_listed: "연재 중이지만 요일 목록에 없음", check_failed: "확인 실패", pending: "상태 확인 대기", catalog_not_loaded: "요일 목록 미확인" },
};
const ETC_PLATFORM_NAMES = { naver: "네이버", kakao: "카카오" };
const etcDiagnosticsCache = {}; // 상태("excluded"/"all") → 요청(Promise) — 같은 결과를 다시 그릴 때마다 요청하지 않는다

function invalidateEtcDiagnostics(status) {
  delete etcDiagnosticsCache[status || "all"];
}

function describeEtcFailures(failedHttp) {
  return Object.entries(failedHttp || {}).map(([key, count]) => `${key === "network" ? "네트워크 오류" : `HTTP ${key}`} ${count}`).join(", ");
}

// 서버가 센 결과를 한 줄 문장으로 — 개수가 0인 플랫폼은 뺀다. 설명할 게 없으면 빈 문자열
function describeEtcDiagnostics(data) {
  const parts = [];
  for (const platform of ["naver", "kakao"]) {
    const side = data[platform];
    if (!side || !side.total) continue;
    const labels = ETC_REASON_LABELS[platform];
    const items = Object.keys(labels)
      .filter((reason) => side.reasons[reason])
      .map((reason) => {
        const detail = reason === "check_failed" ? describeEtcFailures(side.failed_http) : "";
        return `${labels[reason]} ${side.reasons[reason]}${detail ? `(${detail})` : ""}`;
      });
    parts.push(`${ETC_PLATFORM_NAMES[platform]} ${side.total}개: ${items.join(" · ")}`);
  }
  return parts.length ? `기타 원인 — ${parts.join(" / ")}` : "";
}

// 마우스를 올렸을 때 보이는 원인별 작품 예시
function describeEtcSamples(data) {
  const lines = [];
  for (const platform of ["naver", "kakao"]) {
    const side = data[platform];
    if (!side) continue;
    for (const [reason, titles] of Object.entries(side.samples || {})) {
      if (titles.length) lines.push(`${ETC_PLATFORM_NAMES[platform]} ${ETC_REASON_LABELS[platform][reason] || reason}: ${titles.join(", ")}`);
    }
  }
  return lines.length ? `예시 —\n${lines.join("\n")}` : "";
}

// 기타 탭을 보는 동안에만 안내를 보여 준다(다른 탭이면 요청 없이 숨김). status: 화면의 상태("excluded" 등), 없으면 전체 상태
async function renderEtcDiagnostics(noteEl, status, activeTab) {
  if (!noteEl) return;
  if (activeTab !== "etc") {
    noteEl.classList.add("hidden");
    return;
  }
  const key = status || "all";
  if (!(key in etcDiagnosticsCache)) {
    const request = apiCall(`/api/diagnostics/weekday-gaps${status ? `?status=${status}` : ""}`);
    etcDiagnosticsCache[key] = request;
    request.catch(() => { if (etcDiagnosticsCache[key] === request) delete etcDiagnosticsCache[key]; }); // 실패는 기억하지 않는다(다음에 다시 시도)
  }
  let data;
  try {
    data = await etcDiagnosticsCache[key];
  } catch (e) {
    noteEl.classList.add("hidden"); // 안내를 못 받아도 목록 표시에는 영향이 없다
    return;
  }
  const stillEtc = noteEl.dataset.etcWanted === key;
  if (!stillEtc) return; // 기다리는 사이에 다른 탭으로 갔다
  const text = describeEtcDiagnostics(data);
  noteEl.textContent = text;
  noteEl.title = describeEtcSamples(data);
  noteEl.classList.toggle("hidden", !text);
}

// 호출하는 쪽: 지금 기타를 보고 있다고 표시한 뒤 그린다(기다리는 사이 탭이 바뀌면 결과를 버리기 위해)
function showEtcDiagnostics(noteEl, status, activeTab) {
  if (!noteEl) return;
  noteEl.dataset.etcWanted = activeTab === "etc" ? status || "all" : "";
  return renderEtcDiagnostics(noteEl, status, activeTab);
}
