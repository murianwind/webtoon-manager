"""리포트를 실제로 보내 보는 테스트(run_test_report / send_batch_report / send_list_alert)는 신규 웹툰 수집 함수를 가짜로 바꿔 둬야 한다.
바꿔 두지 않으면 수집이 실제 네이버/카카오 목록을 부르는데, 네트워크가 막힌 곳(개발 샌드박스)에서는 조용히 빈 목록이 돼서 통과하고 네트워크가 되는 곳(CI)에서는 실제 신작이
섞여 들어와 실패한다(실제로 test_report_job이 CI에서만 실패했다). 그래서 그런 테스트가 수집 함수를 가짜로 바꾸는지 소스를 보고 확인한다."""
import re
from pathlib import Path

SEND_ENTRY = re.compile(r"run_test_report|send_batch_report|send_list_alert|_run_report\(")
FAKED = ("collect_naver_new_titles", "collect_kakao_new_titles")
WHOLE = "collect_new_title_section"                                    # 섹션 수집을 통째로 바꾸는 것도 인정

offenders = []
for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    if path.name == Path(__file__).name:
        continue
    source = path.read_text(encoding="utf-8")
    if SEND_ENTRY.search(source) and not (all(name in source for name in FAKED) or WHOLE in source):
        offenders.append(path.name)
assert not offenders, f"리포트를 보내 보면서 신규 웹툰 수집을 가짜로 바꾸지 않은 테스트(실제 네트워크를 부를 수 있다): {offenders}"
print("ALL OK")
