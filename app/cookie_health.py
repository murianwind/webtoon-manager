"""
성인 인증 쿠키(NID_AUT 등) 만료를 별도 API 호출 없이, **이번 다운로드 실행에서
실제로 시도한 성인 웹툰들의 결과**로 판단한다 — 네이버에 로그인상태 확인 API를
매번 추가로 호출하는 대신, 이미 하고 있는 회차 목록 조회 결과를 그대로 신호로
쓴다(호출 횟수를 늘리지 않기 위함).

판단 기준: 이번 실행에서 성인 웹툰을 하나라도 시도했는데, 시도한 성인 웹툰 **전부**가
회차 목록을 하나도 못 가져왔다면(전멸) 쿠키 문제로 본다. 하나라도 성공했으면(일부만
비어있는 정도는) 쿠키 문제가 아니라 그 작품 개별 사정으로 보고 무시한다 — 오탐을
줄이기 위한 최소한의 안전장치.

한 번 알리면 복구되기 전까지는 다시 안 알린다(설정 테이블에 플래그 저장). 복구되면
플래그를 지워서, 나중에 또 만료되면 다시 1회 알린다.
"""

import logging

import aiohttp

from app import discord_notify, repository
from app.config import Settings

log = logging.getLogger(__name__)

_SETTING_KEY_EXPIRED_NOTIFIED = "adult_cookie_expired_notified"


class AdultFetchTracker:
    """다운로드 잡 한 번 실행하는 동안, 시도한 성인 웹툰들의 회차 목록 조회 결과를 모은다."""

    def __init__(self) -> None:
        self.attempted = 0
        self.got_episodes = 0

    def record(self, episode_count: int) -> None:
        self.attempted += 1
        if episode_count > 0:
            self.got_episodes += 1

    @property
    def all_failed(self) -> bool:
        """시도한 성인 웹툰이 있고, 전부 회차를 하나도 못 가져왔으면 True."""
        return self.attempted > 0 and self.got_episodes == 0


async def finalize_and_notify(
    session: aiohttp.ClientSession, settings: Settings, tracker: AdultFetchTracker
) -> None:
    """다운로드 잡이 끝날 때 1회 호출 — 이번 실행 결과를 보고 필요하면 알린다."""
    if tracker.attempted == 0:
        return  # 이번 실행에 성인 웹툰을 하나도 안 건드렸으면 판단할 근거가 없음

    already_notified = repository.get_setting(_SETTING_KEY_EXPIRED_NOTIFIED) == "1"

    if tracker.all_failed and not already_notified:
        message = (
            "🍪 **네이버 쿠키 만료 의심**\n"
            f"이번 다운로드에서 성인 인증이 필요한 웹툰 {tracker.attempted}개를 전부 확인했는데, "
            "회차 목록을 하나도 가져오지 못했습니다.\n"
            "브라우저에서 comic.naver.com에 다시 로그인한 뒤, 쿠키 파일을 새로 export해서 교체해주세요."
        )
        await discord_notify.send_webhook_notification(session, settings, message)
        repository.set_setting(_SETTING_KEY_EXPIRED_NOTIFIED, "1")
        log.warning("쿠키 만료 의심 — 디스코드로 알림 전송")
    elif not tracker.all_failed and already_notified:
        repository.set_setting(_SETTING_KEY_EXPIRED_NOTIFIED, None)
        log.info("성인 웹툰 회차 조회가 다시 정상 동작함 — 만료 알림 플래그 초기화")
