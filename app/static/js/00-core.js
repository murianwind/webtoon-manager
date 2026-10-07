// 공용: 상수, apiCall, 포맷/DOM 헬퍼, 뷰어/카카오 사용 여부 플래그
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
const STATUS_LABEL = {
  active: "구독중",
  unsubscribed: "구독해제",
  excluded: "목록제외",
};

const DAY_LABEL = { mon: "월", tue: "화", wed: "수", thu: "목", fri: "금", sat: "토", sun: "일" };
const SCHEDULE_JOB_IDS = ["discovery_job", "download_job", "report_job", "archive_job"];

// 구독/구독해제/제외/목록으로/삭제처럼 웹툰의 상태를 바꾸는 요청 — 성공하면 전체목록/구독해제/제외됨 목록 캐시를 모두 무효화한다
// (각 탭이 60초 동안 다시 받지 않도록 캐시하므로, 다른 탭에서 바꾼 결과가 안 보이는 일이 없게 여기서 한 번에 처리한다).
const LIST_STATE_PATHS = ["/api/webtoons", "/api/kakao-webtoons", "/api/naver-list"];

function changesWebtoonState(path, method) {
  if (method === "GET") return false;
  const base = path.split("?")[0];
  return LIST_STATE_PATHS.some((p) => base === p || base.startsWith(`${p}/`));
}

async function apiCall(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join(", ")
      : detail || `요청 실패 (${res.status})`;
    throw new Error(message);
  }
  if (changesWebtoonState(path, (options.method || "GET").toUpperCase())) invalidateListCaches();
  return res.json();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// 로그의 타임스탬프는 서버가 UTC로 찍어서 보내므로(예: "...+00:00"), 그냥 문자열을
// 잘라 쓰면 한국시간이 아니라 UTC 그대로 보인다 — 항상 이 함수로 KST 변환해서 쓴다.
function formatKoreanTime(isoString, options = {}) {
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return isoString || "";
  return date.toLocaleString("ko-KR", { timeZone: "Asia/Seoul", ...options });
}

function formatKoreanTimeOnly(isoString) {
  return formatKoreanTime(isoString, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

// 페이지마다 표 위쪽에 오는 내용(검색결과, 버튼 줄 등)의 높이가 달라서, 고정된
// px값으로는 어떤 페이지는 남고 어떤 페이지는 모자란다 — 실제 남은 화면 높이를
// 매번 계산해서 정확히 맞춘다("한 화면에 다 보이게" 요구사항 대응).
function fitScrollWrapperToViewport(wrapperId, reserveBelowPx = 16) {
  const wrapper = document.getElementById(wrapperId);
  if (!wrapper) return;
  const top = wrapper.getBoundingClientRect().top;
  const available = window.innerHeight - top - reserveBelowPx;
  wrapper.style.maxHeight = `${Math.max(120, available)}px`;
}

window.addEventListener("resize", () => {
  if (!document.getElementById("page-manual-download").classList.contains("hidden")) {
    fitScrollWrapperToViewport("manual-table-wrapper", 240);
  }
});

// 검색창 지우기(×) 버튼 — class="clearable"인 입력 칸마다 래퍼와 버튼을 붙인다(여러 번 불러도 한 번만). 보이고 숨기는 건 CSS(:placeholder-shown)가 하므로,
// 앱 시작 때 저장된 검색어를 코드로 채워도 버튼이 알아서 맞게 보인다. 누르면 비우고 input 이벤트를 보내서, 검색어 입력 때와 같이 목록이 다시 그려진다.
function initClearableInputs() {
  document.querySelectorAll("input.clearable").forEach((input) => {
    if (input.parentElement.classList.contains("clearable-wrap")) return;
    const wrap = document.createElement("span");
    wrap.className = "clearable-wrap";
    input.parentElement.insertBefore(wrap, input);
    wrap.appendChild(input);
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "input-clear";
    clear.setAttribute("aria-label", "검색어 지우기");
    clear.textContent = "×";
    clear.addEventListener("click", () => {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    wrap.appendChild(clear);
  });
}

function makeButton(label, onClick) {
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function makeIconButton(svgMarkup, title, onClick) {
  const btn = document.createElement("button");
  btn.className = "icon-btn";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.innerHTML = svgMarkup;
  btn.addEventListener("click", onClick);
  return btn;
}

const READER_ICON_SVG = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`;

function naverUrl(titleId) {
  return `https://comic.naver.com/webtoon/list?titleId=${titleId}`;
}

function kakaoContentUrl(titleId) {
  return `https://page.kakao.com/content/${titleId}`;
}

// 웹툰 뷰어 서버 주소가 설정되어 있는지 — 있으면 구독중 카드에 "뷰어에서 보기" 아이콘을 띄운다.
// 페이지 열릴 때 한 번만 확인하고(설정 탭에서 바꾸면 새로고침해야 반영됨), 카드마다
// 매번 물어보진 않는다.
let webtoonServerConfigured = false;
apiCall("/api/settings/webtoon-server")
  .then((data) => { webtoonServerConfigured = Boolean(data.webtoon_server_url); })
  .catch(() => {});

let kakaoWebtoonsEnabled = false;
apiCall("/api/settings/kakao-webtoons-enabled")
  .then((data) => { kakaoWebtoonsEnabled = data.enabled; })
  .catch(() => {});

async function openInWebtoonServer(title, platform) {
  try {
    const data = await apiCall(
      `/api/webtoon-server/lookup?title=${encodeURIComponent(title)}&platform=${encodeURIComponent(platform || "naver")}`
    );
    if (data.url) {
      window.open(data.url, "_blank", "noopener");
    } else {
      alert("뷰어 서버에서 이 작품을 찾지 못했습니다.");
    }
  } catch (e) {
    alert(`뷰어 서버 조회 실패: ${e.message}`);
  }
}

function badgesHtml(w) {
  const parts = [];
  if (w.is_new) parts.push('<span class="badge new-release">신작</span>');
  if (w.is_finished) parts.push('<span class="badge finished">완결</span>');
  if (w.is_paused) parts.push('<span class="badge paused">휴재</span>');
  if (w.has_new_episode) parts.push('<span class="badge new-episode">UP</span>');
  return parts.join("");
}
