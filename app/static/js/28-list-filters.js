// 목록 화면 공용 거르기(뱃지 필터, 검색, 플랫폼 필터)와 모바일 필터 접기/펴기
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)

// 카카오 관리를 끈 상태에서는 카카오 작품이 없으니 플랫폼 필터가 필요 없다 — 숨기고, 숨긴 필터가 목록을 거르지 않게 값도 비운다
function syncPlatformFilter(select) {
  select.classList.toggle("hidden", !kakaoWebtoonsEnabled);
  if (!kakaoWebtoonsEnabled) select.value = "";
  return select.value;
}

// 뱃지 필터("new"=신작, "paused"=휴재, "up"=새 에피소드). 비었거나 모르는 값이면 거르지 않는다.
function matchesBadgeFilter(row, filter) {
  if (filter === "new") return Boolean(row.is_new);
  if (filter === "paused") return Boolean(row.is_paused);
  if (filter === "up") return Boolean(row.has_new_episode);
  return true;
}

// 검색 — query는 소문자로 바꿔서 넘긴다. 제목과 저자 요약에서 찾고, includeWriters면 작가 이름도 본다(구독해제/제외됨).
function matchesSearch(row, query, includeWriters) {
  if (!query) return true;
  return (
    row.title.toLowerCase().includes(query) ||
    (row.author_summary || "").toLowerCase().includes(query) ||
    (includeWriters && (row.writer_names || []).some((name) => name.toLowerCase().includes(query)))
  );
}

// 플랫폼(플랫폼 값이 없으면 네이버)과 뱃지 필터를 한 번에 — 세 목록 화면이 같이 쓴다.
function applyCommonFilters(rows, { platform, badge }) {
  return rows.filter((row) => (!platform || (row.platform || "naver") === platform) && matchesBadgeFilter(row, badge));
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

