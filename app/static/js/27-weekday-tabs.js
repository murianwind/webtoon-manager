// 요일 탭(전체목록/제외됨 공용) — 오늘 요일이 기본, 선택/오늘 표시, 작품이 없는 "기타" 숨김, 탭에 맞는 작품만 고르기
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다. 화면별 상태(listWindow)는 25-list-view.js)

const WEEKDAY_TABS = [
  { key: "mon", label: "월" }, { key: "tue", label: "화" }, { key: "wed", label: "수" }, { key: "thu", label: "목" },
  { key: "fri", label: "금" }, { key: "sat", label: "토" }, { key: "sun", label: "일" },
  { key: "etc", label: "기타" }, // 요일별 목록에 없는 작품(완결·장기 휴재 등) — 요일 정보가 없다
  { key: "all", label: "전체" },
];
const _SEOUL_WEEKDAY_KEYS = { Mon: "mon", Tue: "tue", Wed: "wed", Thu: "thu", Fri: "fri", Sat: "sat", Sun: "sun" };

let currentDate = () => new Date(); // "오늘"을 알려 주는 곳 — 테스트에서 바꿀 수 있게 따로 뒀다

// 한국시간 기준 요일(mon~sun)
function weekdayKeyOf(date) {
  return _SEOUL_WEEKDAY_KEYS[new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Seoul", weekday: "short" }).format(date)];
}

// 처음 쓸 때 "오늘 요일"이 기본이고, 그 뒤로는 사용자가 고른 탭을 유지한다(새로고침하면 다시 오늘)
function activeWeekdayTab(ctx) {
  const state = listWindow(ctx);
  if (!state.tab) state.tab = weekdayKeyOf(currentDate());
  return state.tab;
}

function weekdayTabMatches(row, tab) {
  if (tab === "all") return true;
  const days = row.weekdays || [];
  return tab === "etc" ? days.length === 0 : days.includes(tab);
}

function weekdayTabCounts(rows) {
  const counts = {};
  for (const { key } of WEEKDAY_TABS) counts[key] = rows.filter((row) => weekdayTabMatches(row, key)).length;
  return counts;
}

// 이번에 보여 줄 탭을 정한다: 검색 중이면 "전체", 아니면 화면이 기억한 탭(처음엔 오늘). 보던 "기타"가 비어서 탭이 사라졌으면 오늘 탭으로 돌아온다.
function resolveActiveWeekdayTab(ctx, query, counts) {
  if (query) return "all";
  const state = listWindow(ctx);
  if (state.tab === "etc" && counts.etc === 0) state.tab = null;
  return activeWeekdayTab(ctx);
}

// 탭 줄을 그린다. 선택한 탭은 파란색(.active), 오늘 요일은 선택 여부와 상관없이 .today로 표시(선택하지 않았을 때 빨간색 — "오늘이 어느 요일인지" 잊지 않게),
// 요일을 알 수 없는 작품이 하나도 없으면 "기타" 탭은 보여 주지 않는다.
function renderWeekdayTabs(container, counts, active, onSelect) {
  container.innerHTML = "";
  const todayKey = weekdayKeyOf(currentDate());
  for (const { key, label } of WEEKDAY_TABS) {
    if (key === "etc" && counts.etc === 0) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "weekday-tab" + (key === active ? " active" : "") + (key === todayKey ? " today" : "");
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(key === active));
    if (key === todayKey) {
      button.setAttribute("aria-current", "date");
      button.title = "오늘";
    }
    button.dataset.tab = key;
    if (key === "etc") button.title = "요일을 알 수 없는 작품(완결, 서비스 종료, 요일을 아직 못 알아낸 휴재 작품 등)";
    button.appendChild(document.createTextNode(label));
    const count = document.createElement("span");
    count.className = "weekday-tab-count";
    count.textContent = String(counts[key]);
    button.appendChild(count);
    button.addEventListener("click", () => onSelect(key));
    container.appendChild(button);
  }
  // 탭 줄이 가로로 넘칠 때(모바일) 선택한 탭이 화면 밖에 가려지지 않게 가운데로 가져온다
  const selected = container.querySelector(".weekday-tab.active");
  if (selected && container.scrollWidth > container.clientWidth) container.scrollLeft = selected.offsetLeft - (container.clientWidth - selected.offsetWidth) / 2;
}

// 요일 탭을 적용한다 — rows(다른 필터를 이미 적용한 목록)로 탭 개수를 세고, 탭 줄을 그리고, 고른 탭의 작품만 돌려준다. 검색 중이면 "전체". 기타를 볼 때는 원인 안내도 보여 준다.
// ctx: 화면 이름(화면마다 고른 탭을 따로 기억한다). 탭을 누르면 고른 탭을 기억하고 그리는 개수를 처음으로 돌린 뒤 onChange()로 다시 그리게 한다.
function applyWeekdayTabs(ctx, rows, { query, tabsEl, etcNoteEl, etcStatus, onChange }) {
  const counts = weekdayTabCounts(rows);
  const activeTab = resolveActiveWeekdayTab(ctx, query, counts);
  showEtcDiagnostics(etcNoteEl, etcStatus, activeTab); // 기타를 볼 때만 "왜 요일을 모르는지" 원인을 보여 준다
  renderWeekdayTabs(tabsEl, counts, activeTab, (key) => {
    listWindow(ctx).tab = key;
    resetListWindow(ctx);
    onChange();
  });
  return rows.filter((row) => weekdayTabMatches(row, activeTab));
}
