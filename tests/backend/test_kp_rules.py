import os
"""폴더 규칙 기반 다운로드 엔진 테스트 — 사용자가 올린 실제 폴더 목록(kakao_webtoon_files.json)의 사례를 그대로 사용."""
import asyncio
import json
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path

from app import db
db.get_connection()
from app import kakao_page_download as kp

real = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_webtoon_files.json"), encoding='utf-8'))

# ── 가짜 HTTP ──
class FakeResp:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class FakeSession:
    def __init__(self, handler): self.handler, self.calls = handler, []
    def get(self, url, params=None, headers=None, timeout=None):
        url = str(url); self.calls.append(("GET", url, dict(params or {}))); return self.handler("GET", url, dict(params or {}))
    def post(self, url, data=None, headers=None, timeout=None): return self.handler("POST", url, {})
_orig_sleep = asyncio.sleep
async def _nosleep(x): await _orig_sleep(0)
kp.asyncio.sleep = _nosleep

E = lambda n, sub=None, acc=True, free=None, hidden=False, expire=None: kp.Episode(
    product_id=9000 + n, title=f"작품 {sub or f'{n}화'}", number=n, subtitle=sub or f"{n}화", is_free=(acc if free is None else free),
    accessible=acc, page_count=3, hidden=hidden, rent_expire=expire)
def existing(*items):  # (번호, 부제목) → ExistingFile
    return [kp.ExistingFile(number=n, subtitle=kp.normalize_subtitle(s), name=f"{n:04d}_{s}#10.zip") for n, s in items]

# ═════ 1. 이름 규칙: 실제 파일과 똑같이 ═════
assert kp.episode_zip_name(384, "384화 복국 (2)") == "0384_384화 복국 (2).zip"          # 새 파일에는 페이지 수를 붙이지 않는다
assert kp.episode_zip_name(1, "프롤로그") == "0001_프롤로그.zip"
assert kp.episode_zip_name(12, "제3화: 시작?") == "0012_제3화_ 시작？.zip"
# 실제 목록의 모든 zip 이름이 이 엔진의 파일 이름 파서로 읽히고, 다시 만들면 똑같은 이름이 된다
pat_ok = 0
for folder, files in real.items():
    for name in files:
        if not name.endswith(".zip"): continue
        f = kp.parse_existing_zip_name(name)
        assert f is not None, name
        m = __import__("re").match(r"^(\d+)_(.+)#(\d+)\.zip$", name)
        # 예전 파일은 #페이지수가 붙어 있지만 번호/부제목은 그대로 읽혀서, 새 규칙 이름은 그 부분만 뺀 것과 같다
        assert kp.episode_zip_name(f.number, m.group(2)) == f"{m.group(1)}_{m.group(2)}.zip", (name, kp.episode_zip_name(f.number, m.group(2)))
        pat_ok += 1
assert pat_ok == 1935 and kp.parse_existing_zip_name("cover.jpg") is None and kp.parse_existing_zip_name("info.xml") is None and kp.parse_existing_zip_name("노트.zip") is None
print(f"1) 파일명 규칙 OK — 실제 zip {pat_ok}개의 번호/부제목을 그대로 읽고, 새 이름은 #페이지수만 뺀 형태")

# ═════ 2. 폴더 스캔 (cover.jpg/info.xml은 세지 않음) ═════
d = Path(tempfile.mkdtemp()); (d / "0043_43화#215.zip").write_bytes(b"x"); (d / "cover.jpg").write_bytes(b"x"); (d / "info.xml").write_text("x"); (d / "메모.zip").write_bytes(b"x")
(d / "0044_44화#10.zip.part").write_bytes(b"x")
files = kp.scan_existing_files(d)
assert [(f.number, f.subtitle) for f in files] == [(43, "43화")] and kp.scan_existing_files(d / "없음") == []
print("2) 폴더 스캔 OK (zip만, 임시/cover/info 제외)")

# ═════ 3. 규칙 1 — 폴더가 없거나 zip이 없으면 전부(프롤로그 포함) 받는다 ═════
eps = [E(1, "프롤로그"), E(2, "1화"), E(3, "2화"), E(4, "3화", acc=False), E(5, "4화", acc=False), E(6, "예정", acc=False, hidden=True)]
plan = kp.plan_by_folder_rules(eps, [])
assert plan.mode == "new_folder" and [e.number for e in plan.to_download] == [1, 2, 3] and [e.number for e in plan.locked] == [4, 5, 6]
assert all(not r.downloaded for r in plan.rows) and 6 in [r.episode.number for r in plan.rows]               # 숨김(= N일 후 무료로 열릴 예정) 회차도 목록에 남고 잠금으로 센다
# 숨김이면 접근 가능으로 표시돼 있어도 받지 않는다(사이트에 안 보이는 회차를 받지 않도록)
plan_h = kp.plan_by_folder_rules([E(1, "1화"), E(2, "2화", hidden=True)], []); assert [e.number for e in plan_h.to_download] == [1] and [e.number for e in plan_h.locked] == [2]
print("3) 규칙1 OK (폴더 없음 → 프롤로그부터 전부, 숨김은 잠금으로 표시하고 받지 않음, 잠긴 회차에서 멈춤)")

# ═════ 4. 규칙 3 — zip이 하나면 그 파일 이후부터 (실제 사례: 개미 43화, 유부녀 킬러 179) ═════
eps = [E(n) for n in range(38, 50)]
plan = kp.plan_by_folder_rules(eps, existing((43, "43화")))
assert plan.mode == "single_marker" and [e.number for e in plan.to_download] == list(range(44, 50))
assert [(r.downloaded, r.before_start) for r in plan.rows if r.episode.number < 43] == [(False, True)] * 5     # 표식 앞 회차는 누락으로 보지 않는다
assert [(r.downloaded, r.before_start) for r in plan.rows if r.episode.number == 43] == [(True, False)]
assert plan.marker.number == 43 and plan.marker.warning is False
# 표식 번호가 사이트 순서와 어긋나 있으면 부제목으로 위치를 다시 찾는다(번호가 한 칸 밀린 경우)
shifted = [E(n, sub=f"{n - 1}화") for n in range(38, 50)]                                  # 사이트에선 43화가 44번
plan = kp.plan_by_folder_rules(shifted, existing((43, "42화")))                                  # 표식은 번호 43, 제목 42화
assert plan.marker.resolved_number == 43 and plan.marker.warning is False                  # 제목(42화)이 사이트의 43번과 일치
plan = kp.plan_by_folder_rules(shifted, existing((43, "43화")))                                  # 제목 43화는 사이트의 44번에 있음
assert plan.marker.resolved_number == 44 and [e.number for e in plan.to_download][0] == 45
plan = kp.plan_by_folder_rules(eps, existing((43, "전혀 다른 제목")))                              # 제목으로도 못 찾으면 번호대로 진행 + 경고
assert plan.marker.warning is True and [e.number for e in plan.to_download][0] == 44
# 마지막 회차가 표식이면 받을 게 없다
assert kp.plan_by_folder_rules(eps, existing((49, "49화"))).to_download == []
print("3-2) 규칙3 OK (표식 이후부터, 번호가 밀려 있어도 제목으로 보정, 못 찾으면 경고)")

# 표식으로 시작한 폴더가 이후 회차를 받아 파일이 여러 개가 돼도, 표식 앞 회차를 누락으로 보고 다시 받지 않는다
grown = kp.plan_by_folder_rules([E(n) for n in range(38, 52)], existing((43, "43화"), (44, "44화"), (45, "45화")))
assert grown.mode == "compare" and [e.number for e in grown.to_download] == list(range(46, 52)) and sum(r.before_start for r in grown.rows) == 5
# 폴더 중간이 비어 있으면(가장 이른 파일 이후) 그건 누락으로 받는다
holes = kp.plan_by_folder_rules([E(n) for n in range(1, 10)], existing((3, "3화"), (4, "4화"), (7, "7화")))
assert [e.number for e in holes.to_download] == [5, 6, 8, 9] and [r.episode.number for r in holes.rows if r.before_start] == [1, 2]
print("3-3) 규칙2 범위 OK (가장 이른 파일 앞은 누락이 아님, 그 이후의 빈 회차는 누락)")

# ═════ 5. 규칙 2 — zip이 여러 개면 누락된 회차를 순서대로 (쌍갑포차 스크린샷 상황) ═════
sang = [E(n, sub=f"{n}화 복국 ({n - 383})", acc=False) for n in range(384, 388)] + [E(n, sub=f"{n}화 복국 ({n - 383})", acc=True, free=False, expire="2026-10-02 12:00:00") for n in range(388, 406)] \
       + [E(n, sub=f"{n}화 백설기 ({n - 405})", acc=False) for n in range(406, 413)] + [E(n, sub=f"{n}화 약밥", acc=True, free=True) for n in range(444, 447)]
have = existing(*[(n, f"{n}화 복국 ({n - 383})") for n in range(384, 406)] + [(n, f"{n}화 약밥") for n in range(444, 447)])
plan = kp.plan_by_folder_rules(sang, have)
assert plan.mode == "compare"
done = {r.episode.number: r.downloaded for r in plan.rows}
assert all(done[n] for n in range(384, 406)) and not any(done[n] for n in range(406, 413)) and all(done[n] for n in range(444, 447))
assert plan.to_download == [] and [e.number for e in plan.locked] == list(range(406, 413))       # 잠긴 회차는 대기 — 이 버전은 대여권을 안 쓴다
states = {r.episode.number: kp.episode_state(r.episode) for r in plan.rows}
assert states[384] == "locked" and states[388] == "owned" and states[444] == "free"                # 스크린샷의 잠금/보유/무료
# 누락 회차: 중간이 비어 있으면 그 회차부터 받는다
plan = kp.plan_by_folder_rules([E(n) for n in range(1, 8)], existing((1, "1화"), (2, "2화"), (5, "5화")))
assert [e.number for e in plan.to_download] == [3, 4, 6, 7]
# 번호는 달라도 부제목이 같으면 받은 것으로 본다(번호 체계가 달랐던 예전 파일)
plan = kp.plan_by_folder_rules([E(n, sub=f"{n}화") for n in range(10, 14)], existing((3, "10화"), (4, "11화")))
assert [e.number for e in plan.to_download] == [12, 13]
print("4) 규칙2 OK (스크린샷 상황 재현: 잠금/보유/무료, 누락 회차 비교, 제목 일치 인정)")

# ═════ 6. 실제로 받기 (규칙 적용 + 이름 + 페이지수) ═════
JPG = b"\xff\xd8\xff\xe0" + b"jpg"
SERIES_ITEM = {"id": 1, "title": "작품", "authors": "연상호,최규석", "description": "줄거리 & <설명>", "sub_category": "드라마", "age_grade": 15, "on_issue": "Y"}
cover_calls = []
def fake_cover(series_id, **k):
    cover_calls.append(series_id); return b"\xff\xd8\xffcover"
kp.kakao_cover.fetch_official_cover_bytes = fake_cover
def site(eps_list):
    def h(m, u, p):
        if "product/list" in u:
            items = [{"cursor_index": e.number, "item": {"product_id": e.product_id, "title": e.title, "is_free": e.is_free, "order_value": e.number, "page_count": 3, "hidden": e.hidden,
                      "service_property": {"purchase_info": {"purchase_type": "not_purchased" if not e.accessible or e.is_free else "rent"}}}} for e in eps_list]
            return FakeResp(200, {"result": {"series_item": SERIES_ITEM, "list": items, "has_next": False}})
        if "viewer/data" in u:
            n = 2 if p["product_id"] % 2 else 4
            files = [{"no": i, "secureUrl": f"https://page-edge.kakao.com/sdownload/resource?kid=k{p['product_id']}_{i}&signature=S"} for i in range(1, n + 1)]
            return FakeResp(200, {"item": {}, "viewer_data": {"imageDownloadData": {"files": files}}})
        return FakeResp(200, body=JPG, ctype="image/jpeg")
    return h
async def t6():
    root = Path(tempfile.mkdtemp())
    cl = kp.KakaoPageClient(FakeSession(site([E(1, "프롤로그"), E(2, "1화"), E(3, "2화"), E(4, "3화", acc=False)])), {}, 10)
    res = await kp.run_download(cl, series_id=1, title="도굴왕: 엔드라인", download_root=str(root), max_episodes=10)
    folder = root / "도굴왕_ 엔드라인"                                                              # 콜론 → 밑줄(카카오 폴더 규칙)
    assert res.downloaded == [1, 2, 3] and res.failed is None and res.plan.mode == "new_folder" and [e.number for e in res.plan.locked] == [4]
    xml = (folder / "info.xml").read_text(encoding="utf-8")
    assert "<Web>https://page.kakao.com/content/1</Web>" in xml and "<Publisher>카카오페이지</Publisher>" in xml and "<AgeRating>15세 이용가</AgeRating>" in xml
    assert "<Writer>연상호, 최규석</Writer>" in xml and "<Genre>드라마</Genre>" in xml and "<SeriesStatus>연재</SeriesStatus>" in xml and "<Title>도굴왕: 엔드라인</Title>" not in xml
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["0001_프롤로그.zip", "0002_1화.zip", "0003_2화.zip", "cover.jpg", "info.xml"], names     # 페이지 수는 안 붙고, info.xml/표지가 같이 생긴다
    # 다시 돌리면 규칙2(비교)로 바뀌고 새로 받을 게 없다
    res2 = await kp.run_download(cl, series_id=1, title="도굴왕: 엔드라인", download_root=str(root), max_episodes=10)
    assert res2.downloaded == [] and res2.plan.mode == "compare"
    # 실패하면 거기서 멈추고 흔적을 안 남긴다
    root2 = Path(tempfile.mkdtemp())
    class Bad(FakeSession):
        def get(self, url, params=None, headers=None, timeout=None):
            url = str(url)
            if "sdownload" in url and "k9002_" in url: return FakeResp(500)
            return super().get(url, params=params, headers=headers, timeout=timeout)
    cl2 = kp.KakaoPageClient(Bad(site([E(1), E(2), E(3)])), {}, 10)
    res3 = await kp.run_download(cl2, series_id=1, title="작품", download_root=str(root2), max_episodes=10)
    assert res3.downloaded == [1] and res3.failed == 2 and not list((root2 / "작품").glob("*.part")) and len(list((root2 / "작품").glob("*.zip"))) == 1
    # max_episodes 제한 / 표식 1개 폴더
    root3 = Path(tempfile.mkdtemp()); (root3 / "작품").mkdir(); (root3 / "작품" / "0002_2화#9.zip").write_bytes(b"x"); (root3 / "작품" / "cover.jpg").write_bytes(b"x")
    cl3 = kp.KakaoPageClient(FakeSession(site([E(n) for n in range(1, 8)])), {}, 10)
    res4 = await kp.run_download(cl3, series_id=1, title="작품", download_root=str(root3), max_episodes=2)
    assert res4.plan.mode == "single_marker" and res4.downloaded == [3, 4]
    print("5) 받기 OK (폴더 생성·콜론 규칙·#페이지수, 재실행 시 비교 모드, 실패 시 멈춤, 표식 폴더, 개수 제한)")
asyncio.run(t6())

# ═════ 7. 수동 선택 다운로드 (분석 표에서 고른 회차) ═════
async def t7():
    root = Path(tempfile.mkdtemp()); folder = root / "작품"; folder.mkdir()
    (folder / "0002_2화#9.zip").write_bytes(b"old"); (folder / "0003_3화#9.zip").write_bytes(b"old")          # zip 2개 → 비교 모드
    eps_list = [E(1), E(2), E(3), E(4, acc=False), E(5)]
    cl = kp.KakaoPageClient(FakeSession(site(eps_list)), {}, 10)
    res = await kp.download_selected(cl, series_id=1, title="작품", numbers=[5, 1, 2, 4], download_root=str(root), on_progress=None)
    assert res.downloaded == [1, 2, 5] and res.replaced == [2] and res.skipped_locked == [4] and res.failed == []   # 이미 받은 2도 다시 받는다, 잠긴 4는 건너뜀
    assert sorted(p.name for p in folder.iterdir() if p.suffix == ".zip") == ["0001_1화.zip", "0002_2화.zip", "0003_3화#9.zip", "0005_5화.zip"]   # 2번의 예전 파일(#9)은 새 파일로 교체
    assert (folder / "0003_3화#9.zip").read_bytes() == b"old"                                                    # 고르지 않은 회차는 그대로
    # 다시 받다가 실패하면 원래 파일이 그대로 남는다
    class Bad(FakeSession):
        def get(self, url, params=None, headers=None, timeout=None):
            url = str(url)
            if "sdownload" in url and "k9003_" in url: return FakeResp(500)
            return super().get(url, params=params, headers=headers, timeout=timeout)
    res_bad = await kp.download_selected(kp.KakaoPageClient(Bad(site(eps_list)), {}, 10), series_id=1, title="작품", numbers=[3], download_root=str(root), on_progress=None)
    assert res_bad.failed == [3] and res_bad.downloaded == [] and (folder / "0003_3화#9.zip").read_bytes() == b"old" and not list(folder.glob("*.part"))
    # zip이 하나뿐인 폴더는 표식 규칙 — 표식 앞 회차는 자동으로는 안 받지만, 직접 고르면 받을 수 있다(표식 자체도 다시 받기 가능)
    root2 = Path(tempfile.mkdtemp()); (root2 / "작품").mkdir(); (root2 / "작품" / "0003_3화#9.zip").write_bytes(b"x")
    cl2 = kp.KakaoPageClient(FakeSession(site([E(n) for n in range(1, 7)])), {}, 10)
    res2 = await kp.download_selected(cl2, series_id=1, title="작품", numbers=[1, 3, 4, 99], download_root=str(root2), on_progress=None)
    assert res2.downloaded == [1, 3, 4] and res2.replaced == [3] and res2.not_found == [99]
    print("6) 수동 선택 다운로드 OK (이미 받은 회차도 다시 받아 교체, 실패하면 원본 유지, 잠긴 회차 건너뜀, 표식 폴더)")
asyncio.run(t7())
# ═════ 8. 카카오 info.xml — 연령 표기(15세/18세/전체이용가)와 표지 ═════
from app import comicinfo
assert [comicinfo.kakao_age_rating(a) for a in (0, 12, 15, 18, 19, None)] == ["전체이용가", "전체이용가", "15세 이용가", "18세 이용가", "18세 이용가", "전체이용가"]
assert "<AgeRating>18세 이용가</AgeRating>" in comicinfo.build_kakao_comicinfo_xml({**SERIES_ITEM, "age_grade": 19}, 1)
assert "<SeriesStatus>완결</SeriesStatus>" in comicinfo.build_kakao_comicinfo_xml({**SERIES_ITEM, "on_issue": "N"}, 1)
xml = comicinfo.build_kakao_comicinfo_xml(SERIES_ITEM, 77)
assert "<Summary>줄거리 &amp; &lt;설명&gt;</Summary>" in xml and "<Web>https://page.kakao.com/content/77</Web>" in xml     # XML 이스케이프
import xml.etree.ElementTree as ET; ET.fromstring(xml)                                                                            # 올바른 XML
async def t8():
    root = Path(tempfile.mkdtemp())
    cl = kp.KakaoPageClient(FakeSession(site([E(1), E(2)])), {}, 10)
    cover_calls.clear()
    await kp.run_download(cl, series_id=1, title=None, download_root=str(root), max_episodes=10)
    assert (root / "작품" / "cover.jpg").read_bytes() == b"\xff\xd8\xffcover" and cover_calls == ["1"]
    await kp.download_selected(cl, series_id=1, title=None, numbers=[1], download_root=str(root))
    assert cover_calls == ["1"]                                                    # 표지가 이미 있으면 다시 받지 않는다
    (root / "작품" / "info.xml").write_text("손으로 고침"); await kp.download_selected(cl, series_id=1, title=None, numbers=[2], download_root=str(root))
    assert "<ComicInfo" in (root / "작품" / "info.xml").read_text(encoding="utf-8")   # info.xml은 받을 때마다 최신 정보로 덮어쓴다(네이버와 같음)
    kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: None                # 표지를 못 받아도 다운로드는 성공
    root2 = Path(tempfile.mkdtemp()); r = await kp.run_download(cl, series_id=1, title=None, download_root=str(root2), max_episodes=10)
    assert r.downloaded == [1, 2] and not (root2 / "작품" / "cover.jpg").exists() and (root2 / "작품" / "info.xml").exists()
    print("7) info.xml/표지 OK (연령 표기, 이스케이프, 표지는 없을 때만, 실패해도 다운로드 성공)")
asyncio.run(t8())
print("\n전부 통과")
