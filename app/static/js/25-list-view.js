// 웹툰 목록 화면(전체목록/제외됨/구독해제) 공용: 화면별 상태, 한 번에 조금씩 그리기(점진 렌더링), 검색 입력 지연
// (요일 탭은 27-weekday-tabs.js, 필터/검색 거르기와 모바일 필터 접기는 28-list-filters.js — 파일 이름 순서대로 로드되고, 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
//
// 왜 나눠 그리나: 작품이 천 개가 넘는 목록을 한 번에 전부 카드로 만들면(카드마다 이미지/버튼/이벤트) 브라우저가 몇 초씩 멎는다. 요일 탭으로 먼저 줄이고,
// 그래도 많으면(전체 탭, 검색 등) LIST_PAGE_SIZE개씩만 만들고 "더 보기"/스크롤로 이어서 만든다 — 카드 만드는 일이 보이는 만큼으로 한정된다.

const LIST_PAGE_SIZE = 48;
const SEARCH_DEBOUNCE_MS = 250;

// 화면마다(전체목록/제외됨/구독해제) 따로: 선택한 요일 탭과 지금까지 그린 개수
const listViewState = {};

function listWindow(ctx) {
  return listViewState[ctx] || (listViewState[ctx] = { tab: null, limit: LIST_PAGE_SIZE });
}

function resetListWindow(ctx) {
  listWindow(ctx).limit = LIST_PAGE_SIZE;
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
