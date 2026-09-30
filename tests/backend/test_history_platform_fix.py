"""이력의 플랫폼 열이 생기기 전(update153 이전)에 기록된 카카오 다운로드 이력은 기본값 'naver'로 남는다 — 시작할 때 바로잡는다."""
from app import db, repository
db.get_connection()

def add(title_id, name, platform="naver"):
    repository.add_episode_history(title_id, name, 1, "1화", "success", platform=platform)
def platforms(): return {r["title_id"]: r["platform"] for r in repository.list_episode_history_since("2000-01-01")}

# 네이버 작품(6자리 id, webtoons 테이블에 있음) / 카카오 작품(8자리 id, 예전 이력이라 naver로 남아 있음) / 이미 올바른 카카오 / 카카오 추적 기록 없는 카카오 id
repository.upsert_new(title_id="845332", title="네이버작품")
repository.upsert_new_kakao_webtoon(61641075, "쌍갑포차", status=repository.STATUS_ACTIVE)
add("845332", "네이버작품"); add("61641075", "쌍갑포차"); add("68239972", "개미(추적 기록 없음)"); add("70601591", "이미카카오", platform="kakao")
add("123456", "번호가 작은 미등록 네이버")
assert platforms() == {"845332": "naver", "61641075": "naver", "68239972": "naver", "70601591": "kakao", "123456": "naver"}

fixed = db.fix_episode_history_platforms()
assert fixed == 2, fixed                                                                   # 8자리 이상이면서 네이버 웹툰 목록에 없는 것만
assert platforms() == {"845332": "naver", "61641075": "kakao", "68239972": "kakao", "70601591": "kakao", "123456": "naver"}
assert db.fix_episode_history_platforms() == 0                                              # 다시 돌려도 안전(멱등)
# 같은 번호가 네이버 웹툰으로도 등록돼 있으면(있을 수 없지만) 네이버로 남긴다
repository.upsert_new(title_id="99999999", title="가상의 긴 번호 네이버"); add("99999999", "가상의 긴 번호 네이버")
assert db.fix_episode_history_platforms() == 0 and platforms()["99999999"] == "naver"
print("이력 플랫폼 바로잡기 OK")
print("\n전부 통과")
