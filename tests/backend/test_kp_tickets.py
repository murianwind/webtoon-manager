import os
"""기다무 + 동영상 제외 + 작가 역할 — 실제 캡처(기다무_대여권_선물권.har, 원작자.har)의 응답 형태를 그대로 쓴다."""
import asyncio
import json
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from app import db
db.get_connection()
from app import kakao_page_download as kp, comicinfo

class R:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
JPG = b"\xff\xd8\xff\xe0jpg"
_orig = asyncio.sleep
async def _ns(x): await _orig(0)
kp.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG

def item(order, title, **k):
    d = {"product_id": 5000 + order, "title": title, "is_free": k.get("free", False), "order_value": order, "page_count": 2, "hidden": False,
         "slide_type": k.get("slide", "SD03"), "waitfree_blocked": k.get("blocked", False), "service_property": {}}
    if k.get("rent"): d["service_property"] = {"purchase_info": {"purchase_type": "rent", "rent_expire_dt": "2099-01-01 00:00:00"}}
    return {"cursor_index": order, "item": d}

class Site:
    """가짜 카카오페이지: 회차 목록, 이용권, 기다무 사용(열린 회차는 대여 중이 된다), 이미지."""
    def __init__(self, items, waitfree=True, ticket_type="RT05", gift=0):
        self.items, self.waitfree, self.ticket_type, self.gift = items, waitfree, ticket_type, gift
        self.posts, self.opened = [], set()
    def series_item(self): return {"id": 1, "title": "작품", "authors": "가,나", "description": "d", "sub_category": "드라마", "age_grade": 0, "on_issue": "Y"}
    def get(self, url, params=None, headers=None, timeout=None):
        url = str(url)
        p = params or {}
        if "product/list" in url:
            L = []
            for e in self.items:
                e = json.loads(json.dumps(e))
                if e["item"]["product_id"] in self.opened: e["item"]["service_property"] = {"purchase_info": {"purchase_type": "rent", "rent_expire_dt": "2099-01-01 00:00:00"}}
                L.append(e)
            return R(data={"result": {"series_item": self.series_item(), "list": L, "has_next": False}})
        if url.endswith("/ticket/my"):
            return R(data={"result_code": 0, "result": {"waitfree": {"charged_complete": self.waitfree, "user_activation": True, "charged_at": "2026-10-01T18:41:44+09:00", "charged_period_by_minute": 1440},
                                                       "my": {"ticket_own_count": 0, "cash_amount": 0, "ticket_rental_count": self.gift}}})
        if "ready_to_use" in url:
            blocked = any(e["item"]["product_id"] == int(p["product_id"]) and e["item"]["waitfree_blocked"] for e in self.items)
            return R(data={"result_code": 0, "result": {"single": {"waitfree_block": blocked, "read_access_type": "RAT1"}, "available": {"ticket_rental_type": self.ticket_type},
                                                       "purchase": {"ticket_own": {"ticket_id": "TKT01", "ticket_type": "TT01", "price": 500}}}})
        if "viewer/data" in url:
            pid = int(p["product_id"]); e = next(x for x in self.items if x["item"]["product_id"] == pid)["item"]
            if not (e["is_free"] or pid in self.opened or e["service_property"]): return R(data={"result_code": 1})   # 잠긴 회차는 이미지가 없다
            return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": i, "secureUrl": f"https://img/{pid}/{i}"} for i in (1, 2)]}}})
        if "content/about" in url:
            return R(data={"result_code": 0, "result": {"author_list": [{"name": "김용회", "role": "writer", "role_display_name": "글"}, {"name": "김용회", "role": "illustrator", "role_display_name": "그림"},
                                                                     {"name": "베르나르 베르베르", "role": "original_author", "role_display_name": "원작"}], "theme_keyword_list": [{"title": "추리물"}, {"title": "가족"}]}})
        return R(body=JPG, ctype="image/jpeg")
    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url.rsplit("/api/", 1)[-1], dict(data or {})))
        if url.endswith("/ticket/use"):
            self.opened.add(int(data["product_id"])); self.waitfree = False
            return R(data={"result_code": 0, "result": {"ticket_uid": "1", "rent_expire_dt": "2099-01-01T00:00:00+09:00", "series_id": 1}})
        return R(data={"result_code": 0})
def client(site): return kp.KakaoPageClient(site, {}, 10)
def names(d): return sorted(p.name for p in Path(d).iterdir() if p.suffix == ".zip")

async def main():
    # ── 1. 동영상 제외 + 번호 당기기 (개미: 1번이 동영상 트레일러) ──
    items = [item(1, "작품 동영상 트레일러", free=True, slide="SD05"), item(2, "작품 1화", free=True), item(3, "작품 2화", free=True), item(4, "작품 3화")]
    _, eps = await client(Site(items)).list_episodes(1)
    assert [(e.number, e.subtitle) for e in eps] == [(1, "1화"), (2, "2화"), (3, "3화")]                      # 트레일러는 빠지고 1화가 1번
    _, eps = await client(Site([item(1, "작품 프롤로그", free=True), item(2, "작품 1화", free=True)])).list_episodes(1)
    assert [e.number for e in eps] == [1, 2]                                                                  # 프롤로그(이미지)는 그대로 번호를 차지
    _, eps = await client(Site([item(1, "작품 1화", free=True), item(2, "작품 동영상", free=True), item(3, "작품 2화", free=True)])).list_episodes(1)
    assert [(e.number, e.subtitle) for e in eps] == [(1, "1화"), (2, "2화")]                                  # 중간 동영상(제목으로 판별)도 뒤 번호를 당긴다
    print("1) 동영상 제외 OK (번호를 당겨 예전 도구의 번호 체계와 맞춤, 프롤로그는 유지)")

    # ── 1-2. 실제 개미 캡처(개미_동영상.har)의 응답으로 확인: 1번이 동영상 트레일러(SD08) ──
    asc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "ant_product_list_asc.json"), encoding="utf-8"))
    real_items = [{"cursor_index": x["cursor_index"], "item": x["item"]} for x in asc["result"]["list"]]
    assert real_items[0]["item"]["slide_type"] == "SD08" and real_items[1]["item"]["slide_type"] == "SD03"
    class Real(Site):
        def series_item(self): return {"id": 68239972, "title": "개미"}
    _, eps = await client(Real(real_items)).list_episodes(68239972)
    assert eps[0].subtitle == "1화" and eps[0].number == 1 and eps[1].subtitle == "2화" and eps[1].number == 2      # 트레일러(order 1)가 빠지고 1화가 1번 = 예전 파일 번호
    assert all(kp.is_video_item(x["item"]) is (x["item"]["slide_type"] != "SD03") for x in real_items) and len(eps) == len(real_items) - 1
    print("1-2) 실제 개미 캡처 OK (SD08 동영상 트레일러 제외, 1화가 1번)")

    # ── 1-3. 목록 페이지 넘김: 계정에 저장된 정렬(첫화부터/최신순)과 무관하게 전부 받는다 (개미 1~24화가 빠지던 버그) ──
    class Paged(Site):
        """실제 서버 동작을 흉내: INIT은 계정의 저장된 정렬을 따르고, cursor_index는 "정렬된 목록 안의 순번"이다."""
        def __init__(self, count, saved_sort):
            super().__init__([item(o, f"작품 {o}화", free=True) for o in range(1, count + 1)])
            self.saved_sort, self.requests = saved_sort, []
        def get(self, url, params=None, headers=None, timeout=None):
            url = str(url)
            if "product/list" not in url: return super().get(url, params, headers, timeout)
            p = params or {}; self.requests.append(dict(p))
            sort = self.saved_sort if p["cursor_direction"] == "INIT" else p.get("sort_type", "desc")
            ordered = sorted(self.items, key=lambda e: e["item"]["order_value"], reverse=(sort == "desc"))
            start = int(p["cursor_index"]); window = int(p["window_size"])
            page = ordered[start:start + window]
            L = [{"cursor_index": start + i + 1, "item": e["item"]} for i, e in enumerate(page)]
            return R(data={"result": {"series_item": self.series_item(), "list": L, "has_next": start + window < len(ordered)}})
    for saved in ("asc", "desc"):                                                     # 쌍갑포차(첫화부터)와 개미(최신순) 두 경우
        site = Paged(51, saved)
        _, eps = await client(site).list_episodes(1)
        assert [e.number for e in eps] == list(range(1, 52)), (saved, [e.number for e in eps][:30])
        assert all(r["cursor_direction"] == "NEXT" and r["sort_type"] == "asc" for r in site.requests), site.requests    # INIT(저장된 정렬 의존)을 쓰지 않는다
    print("1-3) 회차 목록 OK (저장된 정렬이 최신순이어도 1화부터 전부, 항상 NEXT+asc)")

    # ── 2. 이용권 조회 ──
    t = await client(Site(items, waitfree=True, gift=2)).ticket_info(1)
    assert (t.rental_count, t.own_count, t.waitfree_ready) == (2, 0, True)
    t = await client(Site(items, waitfree=False)).ticket_info(1); assert t.waitfree_ready is False and t.waitfree_available_at
    print("2) 이용권 조회 OK (대여권/소장권/기다무 상태)")

    # ── 3. 자동: 잠긴 첫 회차를 기다무로 받고 그 뒤는 대기 ──
    locked = [item(1, "작품 1화", free=True), item(2, "작품 2화"), item(3, "작품 3화")]
    root = Path(tempfile.mkdtemp()); site = Site(locked)
    r = await kp.run_download(client(site), series_id=1, title=None, download_root=str(root), max_episodes=20)
    assert r.downloaded == [1, 2] and r.ticket_used == 2 and r.failed is None
    assert names(root / "작품") == ["0001_1화.zip", "0002_2화.zip"] and site.posts == [("v1/ticket/use", {"product_id": 5002, "ticket_type": "RT05"})]     # RT05만, 한 번만
    assert "<Writer>김용회</Writer>" in (root / "작품" / "info.xml").read_text(encoding="utf-8")
    r = await kp.run_download(client(site), series_id=1, title=None, download_root=str(root), max_episodes=20)   # 다시 실행: 기다무가 없으니 3화는 대기
    assert r.downloaded == [] and r.ticket_used is None and len(site.posts) == 1
    site2 = Site(locked, waitfree=False); root2 = Path(tempfile.mkdtemp())                                     # 기다무가 없으면 잠긴 회차에서 멈춘다(예전과 같음)
    r = await kp.run_download(client(site2), series_id=1, title=None, download_root=str(root2), max_episodes=20)
    assert r.downloaded == [1] and r.ticket_used is None and site2.posts == []
    site3 = Site([item(1, "작품 1화", free=True), item(2, "작품 2화", blocked=True)]); root3 = Path(tempfile.mkdtemp())    # 기다무로 열 수 없는 회차(최신 회차)
    r = await kp.run_download(client(site3), series_id=1, title=None, download_root=str(root3), max_episodes=20)
    assert r.downloaded == [1] and site3.posts == []
    print("3) 자동 OK (잠긴 첫 회차만 기다무로 받음, 한 장이라 그 뒤는 대기, 없거나 막힌 회차는 건드리지 않음)")

    # ── 4. 선물권/결제는 절대 쓰지 않는다 ──
    for kind in ("RT06", "TT01", ""):
        s = Site(locked, ticket_type=kind, gift=3); root4 = Path(tempfile.mkdtemp())
        r = await kp.run_download(client(s), series_id=1, title=None, download_root=str(root4), max_episodes=20)
        assert r.downloaded == [1] and s.posts == [], (kind, s.posts)
    print("4) RT05 이외에는 쓰지 않음 OK (선물권 RT06, 결제형 TT01, 빈 값 모두 ticket/use를 부르지 않음)")

    # ── 5. 수동: 잠긴 회차를 골라 받기 (기다무 한 장) ──
    site = Site([item(1, "작품 1화", free=True), item(2, "작품 2화"), item(3, "작품 3화")]); root = Path(tempfile.mkdtemp())
    res = await kp.download_selected(client(site), series_id=1, title=None, numbers=[3, 1, 2], download_root=str(root))
    assert res.downloaded == [1, 2] and res.ticket_used == [2] and res.skipped_locked == [3]                  # 번호가 앞선 2화만 열고 3화는 건너뜀
    assert [p[1]["ticket_type"] for p in site.posts] == ["RT05"]
    res = await kp.download_selected(client(Site([item(1, "작품 1화"), item(2, "작품 2화")], waitfree=False)), series_id=1, title=None, numbers=[1], download_root=str(Path(tempfile.mkdtemp())))
    assert res.downloaded == [] and res.skipped_locked == [1] and res.ticket_used == []
    # 사용자가 웹에서 기다무/선물권을 이미 써서 '대여 중'이 된 회차는 이용권 없이 그대로 받는다
    site = Site([item(1, "작품 1화", free=True), item(2, "작품 2화", rent=True)], waitfree=False); root = Path(tempfile.mkdtemp())
    res = await kp.download_selected(client(site), series_id=1, title=None, numbers=[1, 2], download_root=str(root))
    assert res.downloaded == [1, 2] and res.ticket_used == [] and site.posts == []
    r = await kp.run_download(client(site), series_id=1, title=None, download_root=str(Path(tempfile.mkdtemp())), max_episodes=20)
    assert r.downloaded == [1, 2] and site.posts == []                                                        # 자동도 대여 중인 회차는 그대로 받는다
    print("5) 수동 OK (잠긴 회차는 기다무 한 장으로 하나만, 웹에서 이미 쓴 대여 회차는 이용권 없이 받음)")

    # ── 5-2. 기다무 주기(3시간/1일/3일)를 이용권 응답에서 읽는다 ──
    class Period(Site):
        def __init__(self, minutes, **k): super().__init__(items, **k); self.minutes = minutes
        def get(self, url, params=None, headers=None, timeout=None):
            r = super().get(url, params, headers, timeout)
            if str(url).endswith("/ticket/my"): r._d["result"]["waitfree"]["charged_period_by_minute"] = self.minutes
            return r
    for minutes in (180, 1440, 4320):
        assert (await client(Period(minutes)).ticket_info(1)).waitfree_period_minutes == minutes
    print("5-2) 기다무 주기 OK (3시간/1일/3일)")

    # ── 5-3. 이미지 받기: 서명된 주소는 그대로 보내고, 첫 이미지로 먼저 확인하고, 안 되면 주소를 새로 받아 한 번 더, 그래도 안 되면 바로 중단 ──
    from yarl import URL
    tricky = "https://dw-img-page.kakao.com/sdownload/resource?token=eTd3c_Kiz-N%2BPF%3D%3D&x=a+b"       # 인코딩된 문자가 들어 있는 토큰 주소
    sent = []
    class Probe(Site):
        def get(self, url, params=None, headers=None, timeout=None):
            if "sdownload" in str(url): sent.append(url); return R(body=JPG, ctype="image/jpeg")
            return super().get(url, params, headers, timeout)
    cl = client(Probe(items)); assert await cl.download_image(tricky) is not None
    assert isinstance(sent[0], URL) and sent[0].raw_query_string == "token=eTd3c_Kiz-N%2BPF%3D%3D&x=a+b" and sent[0].host == "dw-img-page.kakao.com"    # 다시 인코딩하지 않는다
    class Flaky(Site):
        """viewer/data 첫 응답의 주소는 무효(404), 두 번째 응답부터 유효 — 서명 주소가 만료/무효일 때의 복구."""
        def __init__(self, items, good_after):
            super().__init__(items); self.good_after, self.viewer_calls, self.image_calls = good_after, 0, []
        def get(self, url, params=None, headers=None, timeout=None):
            u = str(url)
            if "viewer/data" in u:
                self.viewer_calls += 1; pid = int(params["product_id"])
                return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": i, "secureUrl": f"https://dw-img-page.kakao.com/sdownload/resource?token=v{self.viewer_calls}_{pid}_{i}"} for i in (1, 2, 3)]}}})
            if "sdownload" in u:
                self.image_calls.append(u); ok = int(u.split("token=v")[1].split("_")[0]) >= self.good_after
                return R(body=JPG, ctype="image/jpeg") if ok else R(status=404, body=b"not found")
            return super().get(url, params, headers, timeout)
    ep1 = kp.Episode(product_id=5001, title="t", number=1, subtitle="1화", is_free=True, accessible=True, page_count=3, hidden=False)
    site = Flaky([item(1, "작품 1화", free=True)], good_after=2); root = Path(tempfile.mkdtemp())
    assert await kp.download_episode(client(site), 1, ep1, root / "작품") is not None and site.viewer_calls == 2      # 첫 주소가 안 되면 주소를 새로 받아 성공
    assert (root / "작품" / "0001_1화.zip").is_file()
    site = Flaky([item(1, "작품 1화", free=True)], good_after=99); root = Path(tempfile.mkdtemp())
    assert await kp.download_episode(client(site), 1, ep1, root / "작품") is None and site.viewer_calls == 2
    assert len(site.image_calls) == 2 * 3 and not list((root / "작품").glob("*")) if (root / "작품").exists() else len(site.image_calls) == 6   # 첫 이미지만 재시도(주소 2번 × 3회), 나머지 이미지는 요청조차 안 함, 흔적 없음
    print("5-3) 이미지 받기 OK (주소 그대로 전송, 첫 이미지 확인, 주소 재발급 복구, 안 되면 바로 중단)")

    # ── 6. 작가 역할 ──
    about = await client(Site(items)).fetch_about(1)
    assert kp.split_authors(about) == (["김용회"], ["김용회"], ["베르나르 베르베르"])
    assert kp.authors_to_register(about) == ["베르나르 베르베르"]                                              # 원작자가 있으면 원작자
    only_writer = {"author_list": [{"name": "글쓴이", "role": "writer"}, {"name": "그린이", "role": "illustrator"}]}
    assert kp.authors_to_register(only_writer) == ["글쓴이"] and kp.authors_to_register(None) == [] and kp.authors_to_register({}) == []
    xml = comicinfo.build_kakao_comicinfo_xml({"title": "개미", "authors": "김용회,베르나르 베르베르", "sub_category": "드라마", "age_grade": 0, "on_issue": "Y"}, 68239972, about)
    assert "<Writer>김용회</Writer>" in xml and "<CoverArtist>김용회</CoverArtist>" in xml and "<Notes>원작: 베르나르 베르베르</Notes>" in xml and "<Tags>추리물,가족</Tags>" in xml
    assert "<Writer>김용회, 베르나르 베르베르</Writer>" in comicinfo.build_kakao_comicinfo_xml({"title": "개미", "authors": "김용회,베르나르 베르베르"}, 1, None)    # 정보 탭을 못 받으면 이름 전부
    print("6) 작가 역할 OK (글/그림/원작 구분, 원작자 우선 등록, info.xml에 반영, 못 받으면 이름 전부)")
asyncio.run(main())
print("\n전부 통과")
