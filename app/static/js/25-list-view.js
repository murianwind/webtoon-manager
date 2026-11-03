// 웹툰 목록 화면(전체목록/제외됨/구독해제) 공용: 요일별 탭, 한 번에 조금씩 그리기(점진 렌더링), 검색 입력 지연
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
//
// 왜 나눠 그리나: 작품이 천 개가 넘는 목록을 한 번에 전부 카드로 만들면(카드마다 이미지/버튼/이벤트) 브라우저가 몇 초씩 멎는다. 요일 탭으로 먼저 줄이고,
// 그래도 많으면(전체 탭, 검색 등) LIST_PAGE_SIZE개씩만 만들고 "더 보기"/스크롤로 이어서 만든다 — 카드 만드는 일이 보이는 만큼으로 한정된다.

const WEEKDAY_TABS = [
  { key: "mon", label: "월" }, { key: "tue", label: "화" }, { key: "wed", label: "수" }, { key: "thu", label: "목" },
  { key: "fri", label: "금" }, { key: "sat", label: "토" }, { key: "sun", label: "일" },
  { key: "etc", label: "기타" }, // 요일별 목록에 없는 작품(완결·장기 휴재 등) — 요일 정보가 없다
  { key: "all", label: "전체" },
];
const LIST_PAGE_SIZE = 48;
const SEARCH_DEBOUNCE_MS = 250;
const _SEOUL_WEEKDAY_KEYS = { Mon: "mon", Tue: "tue", Wed: "wed", Thu: "thu", Fri: "fri", Sat: "sat", Sun: "sun" };

let currentDate = () => new Date(); // "오늘"을 알려 주는 곳 — 테스트에서 바꿀 수 있게 따로 뒀다

// 한국시간 기준 요일(mon~sun)
function weekdayKeyOf(date) {
  return _SEOUL_WEEKDAY_KEYS[new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Seoul", weekday: "short" }).format(date)];
}

// 화면마다(전체목록/제외됨/구독해제) 따로: 선택한 요일 탭과 지금까지 그린 개수
const listViewState = {};

function listWindow(ctx) {
  return listViewState[ctx] || (listViewState[ctx] = { tab: null, limit: LIST_PAGE_SIZE });
}

// 처음 쓸 때 "오늘 요일"이 기본이고, 그 뒤로는 사용자가 고른 탭을 유지한다(새로고침하면 다시 오늘)
function activeWeekdayTab(ctx) {
  const state = listWindow(ctx);
  if (!state.tab) state.tab = weekdayKeyOf(currentDate());
  return state.tab;
}

function resetListWindow(ctx) {
  listWindow(ctx).limit = LIST_PAGE_SIZE;
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

// 카카오 관리를 끈 상태에서는 카카오 작품이 없으니 플랫폼 필터가 필요 없다 — 숨기고, 숨긴 필터가 목록을 거르지 않게 값도 비운다
function syncPlatformFilter(select) {
  select.classList.toggle("hidden", !kakaoWebtoonsEnabled);
  if (!kakaoWebtoonsEnabled) select.value = "";
  return select.value;
}

// 지금 보여줄 항목(rows) 중 처음 limit개만 돌려주고, 나머지는 grid 바로 아래 "더 보기" 줄로 이어 그린다(버튼, 그리고 화면 가까이 오면 자동).
function windowRows(ctx, grid, rows, rerender) {
  const state = listWindow(ctx);
  const shown = rows.slice(0, state.limit);
  updateMoreControl(grid, rows.length - shown.length, () => {
    state.limit += LIST_PAGE_SIZE;
    rerender();
    if (grid._moreObserver) {
      // 아직도 화면 가까이에 있으면 다음 쪽도 이어서 그린다(관찰자는 상태가 "바뀔 때"만 알려 주므로 다시 붙여서 현재 상태를 확인하게 한다)
      grid._moreObserver.unobserve(grid._moreBox);
      grid._moreObserver.observe(grid._moreBox);
    }
  });
  return shown;
}

function updateMoreControl(grid, remaining, onMore) {
  if (!grid._moreBox) {
    const box = document.createElement("div");
    box.className = "list-more hidden";
    const button = document.createElement("button");
    button.type = "button";
    button.addEventListener("click", () => grid._onMore && grid._onMore());
    box.appendChild(button);
    grid.insertAdjacentElement("afterend", box);
    grid._moreBox = box;
    grid._moreButton = button;
    if (typeof IntersectionObserver !== "undefined") {
      grid._moreObserver = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting) && grid._onMore) grid._onMore();
        },
        { rootMargin: "800px 0px" } // 맨 아래에 닿기 전에 미리 이어서 그린다
      );
      grid._moreObserver.observe(box);
    }
  }
  grid._onMore = onMore;
  grid._moreBox.classList.toggle("hidden", remaining <= 0);
  grid._moreButton.textContent = `더 보기 (남은 ${remaining}개)`;
}

// 입력이 멈춘 뒤 ms 뒤에 한 번만 실행 — 검색창에 글자를 칠 때마다 수백 개 카드를 다시 걸러서 그리지 않게
function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ── 모바일: 필터 접기/펴기 ─────────────────────────────────────────
// 모바일에서는 검색창 아래에 "필터" 버튼만 두고 필터 선택들과 마지막 확인 시각은 접어 둔다(CSS가 .filters-open일 때만 펼친다). 데스크톱에서는 이 버튼이 안 보인다.
// 접힌 상태에서도 어떤 필터가 걸려 있는지 알 수 있게 버튼에 "N개 적용"을 보여 준다 — 정렬은 필터가 아니라서 세지 않고(data-filter-ignore), 숨겨진 선택(예: 네이버가 아닐 때의 태그)도 세지 않는다.
function updateFilterToggleLabels() {
  document.querySelectorAll(".page").forEach((page) => {
    const button = page.querySelector(".filter-toggle");
    if (!button) return;
    const applied = [...page.querySelectorAll(".list-filters select")].filter(
      (select) => !select.hasAttribute("data-filter-ignore") && !select.classList.contains("hidden") && select.value !== "" && select.value !== "all"
    ).length;
    const open = page.classList.contains("filters-open");
    button.querySelector(".filter-toggle-label").textContent = applied > 0 ? `필터 · ${applied}개 적용` : "필터";
    button.querySelector(".filter-toggle-arrow").textContent = open ? "▴" : "▾";
    button.setAttribute("aria-expanded", String(open));
  });
}

document.querySelectorAll(".filter-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".page").classList.toggle("filters-open");
    updateFilterToggleLabels();
  });
});
document.querySelectorAll(".list-filters").forEach((box) => box.addEventListener("change", updateFilterToggleLabels));

