"""카카오페이지 연령 등급 — 실제 응답의 age_grade는 정수(0/15/19)다. 타입이 바뀌어도(문자열 등) 죽지 않는지도 확인한다."""
import json, os
from app import comicinfo

S = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_page_samples.json"), encoding="utf-8"))
real_item = S["content_product_list"]["result"]["series_item"]
assert isinstance(real_item["age_grade"], int), real_item["age_grade"]                          # 실제 응답(캡처)의 age_grade는 정수
assert comicinfo.kakao_age_rating(real_item["age_grade"]) == "전체이용가"
for raw, expected in (("0", "전체이용가"), ("12", "전체이용가"), ("15", "15세 이용가"), ("18", "18세 이용가"), ("19", "18세 이용가"),
                      (0, "전체이용가"), (15, "15세 이용가"), (19, "18세 이용가"), (None, "전체이용가"), ("", "전체이용가"), ("ALL", "전체이용가"), (" 15 ", "15세 이용가")):
    assert comicinfo.kakao_age_rating(raw) == expected, (raw, comicinfo.kakao_age_rating(raw))   # 문자열/정수/빈 값/이상한 값 모두 죽지 않는다
xml = comicinfo.build_kakao_comicinfo_xml(real_item, 70601591, None)                              # 실제 응답 그대로 info.xml을 만들 수 있다
assert "<AgeRating>전체이용가</AgeRating>" in xml and "<Web>https://page.kakao.com/content/70601591</Web>" in xml and "<Writer>연상호, 최규석</Writer>" in xml
print("연령 등급 OK (실제 응답 형태: 정수, 문자열/빈 값도 안전)")
print("\n전부 통과")
