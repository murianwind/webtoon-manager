"""
다운로드 폴더 해석 — 네이버와 카카오페이지를 서로 다른 폴더에 받을 수 있게, "지금 실제로 쓰는 폴더"를 한 곳에서 정한다.

- 네이버 폴더: 설정(naver_download_root)이 있으면 그것, 없으면 환경변수 DOWNLOAD_ROOT.
- 카카오페이지 폴더: 설정(kakao_download_root)이 있으면 그것, 없으면 네이버 폴더(같은 폴더에 받던 예전 동작).
- 아카이빙의 폴더/일괄 이동은 폴더를 이름표("archive" | "download" | "kakao_download")로 가리킨다. "download"는 네이버 폴더다.
  웹툰 유형 아카이빙 대상은 실행할 때마다 폴더를 새로 받아 쓰므로 폴더를 바꿔도 자동으로 따라간다(옮기는 건 사용자 몫).
"""

from app import kakao_page_download, repository

NAVER_ROOT_SETTING_KEY = "naver_download_root"
LOCAL_ROOT_NAMES = ("archive", "download", "kakao_download")


def naver_root(settings) -> str:
    return repository.get_setting(NAVER_ROOT_SETTING_KEY) or settings.download_root


def kakao_root(settings) -> str:
    return kakao_page_download.effective_download_root(naver_root(settings))


def local_root_path(name: str, settings) -> str:
    """로컬 폴더 이름표 → 실제 경로."""
    if name == "archive":
        return settings.archive_root
    return kakao_root(settings) if name == "kakao_download" else naver_root(settings)
