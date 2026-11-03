// 화면 테스트 공용 작품 만들기 — 전체목록/구독해제/제외됨 화면이 받는 항목 모양.
// 예제 작품은 모든 요일에 연재하는 것으로 둔다(요일별 탭이 생겨서 어느 요일 탭에서든 보이게). 요일이 중요한 테스트는 extra로 weekdays를 덮어쓴다.
const ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// 전체목록 응답의 네이버 작품(/api/naver-list). 구독 상태 등은 extra로 바꾼다.
const naver = (id, title, extra = {}) => ({
  title_id: String(id), title, weekdays: ALL_DAYS, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가",
  is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra,
});

// 카카오 목록 응답의 작품(/api/kakao-list의 items). title_id는 숫자.
const kakao = (id, title, extra = {}) => ({
  title_id: id, title, weekdays: ALL_DAYS, thumbnail_url: `https://k/${id}`, author_summary: "작가",
  is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra,
});

// 이미 구독 중인 작품으로 만드는 변형(구독해제/뷰어 아이콘처럼 "구독 중"이 기본인 테스트용)
const subscribed = (make) => (id, title, extra = {}) => make(id, title, { status: "active", ever_subscribed: true, ...extra });

// 화면 목록 한 줄(플랫폼을 함께 들고 있는 형태 — 전체목록 캐시/제외됨·구독해제 캐시가 쓰는 모양)
const listItem = (platform, id, title, weekdays, extra = {}) => ({
  platform, title_id: platform === "kakao" ? 70000000 + id : String(id), title, thumbnail_url: "", author_summary: "저자", writer_names: ["저자"],
  tags: [], weekdays, status: null, ever_subscribed: false, is_new: false, is_paused: false, has_new_episode: false, is_adult: false, is_finished: false, ...extra,
});

module.exports = { ALL_DAYS, naver, kakao, subscribed, listItem };
