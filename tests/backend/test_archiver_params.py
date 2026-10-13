"""아카이빙 매개변수 객체 — 인자 14~16개이던 함수들을 값 묶음으로 바꾼 것(app/archiver/params.py)의 규칙을 고정한다.

- ProgressReport: 진행 문구/이름 충돌/이동 실패를 호출부에 알리는 통로. 셋 다 선택이라 지정한 것만 기록하고, 지정하지 않아도 호출한 쪽은 아무 확인 없이 부를 수 있다.
- TitleArchiveJob: 작품 하나를 아카이빙하는 데 필요한 값(기본값: 로컬 보관, rclone 설정 없음, 글 작가 없음, 일반(네이버) 폴더 이름 규칙).
"""
import dataclasses
import tempfile

from app.archiver.params import FolderMove, ProgressReport, TitleArchiveJob, TitleDestination

# Scenario 1: 지정한 통로에만 기록한다(진행 문구는 호출, 충돌/실패는 목록에 추가)
lines, conflicts, failures = [], [], []
report = ProgressReport(progress_callback=lines.append, conflict_log=conflicts, failure_log=failures)
report.progress("[작품] 이동 완료: 0001.zip"); report.conflict("작품", "0002.zip", "skip"); report.failure("작품", "0003.zip", "디스크 오류")
assert lines == ["[작품] 이동 완료: 0001.zip"] and conflicts == [("작품", "0002.zip", "skip")] and failures == [("작품", "0003.zip", "디스크 오류")]
# Scenario 2: 아무것도 지정하지 않아도 오류 없이 지나간다(호출부에서 if로 확인하지 않는다)
empty = ProgressReport(); empty.progress("x"); empty.conflict("a", "b", "c"); empty.failure("a", "b", "c")
# Scenario 3: 일부만 지정하면 그것만 쓴다
only_failures = []
partial = ProgressReport(failure_log=only_failures); partial.progress("무시"); partial.conflict("a", "b", "c"); partial.failure("작품", "x.zip", "오류")
assert only_failures == [("작품", "x.zip", "오류")]
# Scenario 4: 작품 아카이빙 값의 기본값 — 필수는 앞의 여덟 개, 나머지는 기본값이 있고 값 묶음은 바뀌지 않는다(frozen)
job = TitleArchiveJob(archive_root=tempfile.mkdtemp(), download_root=tempfile.mkdtemp(), title_id="77", title_name="작품", base_path="보관", policy="skip", trigger_type="manual", keep_last=True)
assert (job.dest_type, job.rclone_config_path, job.writer_names, job.filename_template_preset_id, job.kakao) == ("local", "", None, None, False)
try:
    job.title_name = "다른 작품"; raise SystemExit("frozen이 아니다")
except dataclasses.FrozenInstanceError:
    pass
assert {f.name for f in dataclasses.fields(TitleDestination)} >= {"title_id", "title_name", "dest_type", "policy", "trigger_type", "template", "writer_names", "rclone_config_path", "remote", "rclone_dest_path", "local_dest_dir"}
assert {f.name for f in dataclasses.fields(FolderMove)} >= {"target", "src_kind", "src_ctx", "dest_kind", "dest_ctx", "policy", "template", "display_name", "trigger_type", "rclone_config_path"}
print("ALL OK")
