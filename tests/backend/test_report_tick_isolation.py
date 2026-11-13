"""1분 확인(report_tick.run_report_tick)의 단계들이 서로 방해하지 않는다 — 특히 웹툰 서버 새로고침(_advance_scan: 다운로드/아카이빙 뒤 뷰어 서버를 다시 훑게 하는 것)은
하루 한 번 도는 웹툰 전체목록 알림(_list_alert_step)이 늦거나 오류를 내도 그대로 진행돼야 한다. 알림 단계의 오류는 기록만 하고 넘어가고, 새로고침 단계가 알림 단계보다 먼저 돈다."""
import asyncio
from datetime import datetime, timezone

from app import db, list_alert
db.get_connection()
from app.scheduler import report_tick

order = []
async def fake_report_step(now, scan): order.append("report")
async def fake_advance_scan(now, scan): order.append("scan")
real = (report_tick._report_step, report_tick._advance_scan, report_tick._list_alert_step)
report_tick._report_step, report_tick._advance_scan = fake_report_step, fake_advance_scan
NOW = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)

# Scenario 1: 순서 — 다운로드 리포트 단계 → 웹툰 서버 새로고침 → 전체목록 알림. 새로고침이 알림(네트워크로 오래 걸릴 수 있다)에 밀리지 않는다
async def fake_alert_step(now): order.append("alert")
report_tick._list_alert_step = fake_alert_step
order.clear(); asyncio.run(report_tick.run_report_tick(NOW)); assert order == ["report", "scan", "alert"], order
print("1) 순서 OK (새로고침이 알림보다 먼저)")

# Scenario 2: 알림 단계가 오류를 내도 1분 확인은 오류 없이 끝난다 — 새로고침은 이미 진행됐고, 다음 분에도 같은 일이 반복되지 않게 잠금도 풀린다
async def boom_alert_step(now): order.append("alert"); raise RuntimeError("알림 설정이 깨졌다")
report_tick._list_alert_step = boom_alert_step
order.clear(); asyncio.run(report_tick.run_report_tick(NOW)); assert order == ["report", "scan", "alert"], order
order.clear(); asyncio.run(report_tick.run_report_tick(NOW)); assert order == ["report", "scan", "alert"], "다음 확인도 그대로 돈다(잠금이 풀려 있다)"
print("2) 알림 단계 오류가 새로고침을 막지 않음 OK")

# Scenario 3: 실제 알림 단계에서 설정 값이 깨져 있어도(예: 다시 시도 시각이 날짜가 아님) 새로고침은 진행된다
report_tick._list_alert_step = real[2]
import app.repository as repository
repository.set_setting(list_alert.SETTING_RETRY, "날짜아님"); repository.set_setting(list_alert.SETTING_TIME, "00:00")
from app import discord_config
discord_config.set_webhook_url("https://discord.test/hook")
order.clear(); asyncio.run(report_tick.run_report_tick(NOW)); assert order == ["report", "scan"], order
print("3) 깨진 설정에서도 새로고침 진행 OK")
report_tick._report_step, report_tick._advance_scan = real[0], real[1]
print("ALL OK")
