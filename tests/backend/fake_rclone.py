"""테스트용 가짜 rclone — 원격(remote)을 메모리 안의 {(remote, 경로): 내용}으로 흉내 낸다. archiver가 쓰는 rclone_client 함수들을 대신한다.

사용: fake = FakeRclone(); fake.install()   (테스트 스크립트 안에서 — 별도 프로세스라 되돌릴 필요 없음)
      fake.files  → {("remote", "a/b/c.zip"): bytes}
"""
import os
from pathlib import Path

from app import rclone_client


def _join(path: str, name: str = "") -> str:
    return "/".join(p for p in (path.strip("/"), name) if p)


class FakeRclone:
    def __init__(self):
        self.files: dict[tuple[str, str], bytes] = {}
        self.folders: set[tuple[str, str]] = set()
        self.fail_move_names: set[str] = set()  # 이 이름의 파일을 원격으로 올릴 때 RcloneError를 낸다
        self.leave_empty_on_fail = False  # 업로드가 실패하면 OneDrive처럼 0KB 자리표시 파일이 목적지에 남는다(정리 요청도 실패하면 그대로 남는다)
        self.fail_delete = False  # 원격 파일 지우기가 실패한다(401 등) — 정리가 실패해도 이동이 망가지지 않는지 본다
        self.deleted: list[str] = []  # 지운 원격 파일(경로) 기록 — 무엇을 지웠는지 확인한다

    # ── 경로 해석 ──
    @staticmethod
    def _spec(spec: str):
        if ":" in spec and not os.path.isabs(spec):
            remote, path = spec.split(":", 1)
            return ("remote", remote, path.strip("/"))
        return ("local", spec)

    def _read(self, spec: str) -> bytes:
        kind = self._spec(spec)
        if kind[0] == "local":
            return Path(kind[1]).read_bytes()
        return self.files[(kind[1], kind[2])]

    def _write(self, spec: str, data: bytes) -> None:
        kind = self._spec(spec)
        if kind[0] == "local":
            Path(kind[1]).parent.mkdir(parents=True, exist_ok=True)
            Path(kind[1]).write_bytes(data)
        else:
            self.files[(kind[1], kind[2])] = data

    # ── rclone_client 함수들 ──
    def list_remotes(self, config_path):
        return sorted({r for r, _ in self.files} | {r for r, _ in self.folders})

    def list_folders(self, config_path, remote, path):
        prefix = path.strip("/")
        names = set()
        for r, p in list(self.folders) + [(r, os.path.dirname(p)) for r, p in self.files]:
            if r == remote and p.startswith(prefix + "/" if prefix else "") and p != prefix:
                rest = p[len(prefix):].strip("/")
                if rest:
                    names.add(rest.split("/")[0])
        return sorted(names)

    def is_folder_empty(self, config_path, remote, path):
        return not any(r == remote and (p == path.strip("/") or p.startswith(path.strip("/") + "/")) for r, p in self.files)

    def create_folder(self, config_path, remote, path):
        self.folders.add((remote, path.strip("/")))

    def move_file_to_remote(self, config_path, local_file_path, remote, dest_path, dest_file_name):
        name = dest_file_name or Path(local_file_path).name
        if name in self.fail_move_names:
            if self.leave_empty_on_fail:
                self.files[(remote, _join(dest_path, name))] = b""
            raise rclone_client.RcloneError(f"업로드 실패: {name}")
        self.files[(remote, _join(dest_path, name))] = Path(local_file_path).read_bytes()
        Path(local_file_path).unlink()

    def file_exists(self, config_path, remote, path, file_name, *, ignore_empty=False):
        data = self.files.get((remote, _join(path, file_name)))
        return data is not None and not (ignore_empty and len(data) == 0)

    def file_size(self, config_path, remote, path, file_name):
        data = self.files.get((remote, _join(path, file_name)))
        return None if data is None else len(data)

    def delete_empty_file(self, config_path, remote, path, file_name):
        key = (remote, _join(path, file_name))
        if self.fail_delete or key not in self.files or len(self.files[key]) != 0:
            return False
        del self.files[key]; self.deleted.append(key[1])
        return True

    def purge_empty_zip_files(self, config_path, remote, path):
        if self.fail_delete:
            return []
        prefix = path.strip("/")
        names = sorted(p[len(prefix):].strip("/") for r, p in self.files if r == remote and os.path.dirname(p) == prefix and p.lower().endswith(".zip") and len(self.files[(r, p)]) == 0)
        for name in names:
            del self.files[(remote, _join(path, name))]; self.deleted.append(_join(path, name))
        return names

    def list_files_recursive(self, config_path, remote, path):
        prefix = path.strip("/")
        return sorted(p[len(prefix):].strip("/") for r, p in self.files if r == remote and (not prefix or p.startswith(prefix + "/")))

    def moveto(self, config_path, src_spec, dest_spec):
        self._write(dest_spec, self._read(src_spec))
        kind = self._spec(src_spec)
        if kind[0] == "local":
            Path(kind[1]).unlink()
        else:
            self.files.pop((kind[1], kind[2]))

    def copyto(self, config_path, src_spec, dest_spec):
        self._write(dest_spec, self._read(src_spec))

    def rmdirs_if_empty(self, config_path, remote, path):
        pass

    def list_top_level_files(self, config_path, remote, path):
        prefix = path.strip("/")
        return sorted(p[len(prefix):].strip("/") for r, p in self.files if r == remote and os.path.dirname(p) == prefix)

    def read_small_text_file(self, config_path, remote, path, file_name):
        data = self.files.get((remote, _join(path, file_name)))
        return None if data is None else data.decode("utf-8")

    def delete_file(self, config_path, remote, path, file_name):
        if self.fail_delete:
            raise rclone_client.RcloneError("삭제 실패(401)")
        self.files.pop((remote, _join(path, file_name)), None)
        self.deleted.append(_join(path, file_name))

    def install(self):
        for name in ("list_remotes", "list_folders", "is_folder_empty", "create_folder", "move_file_to_remote", "file_exists", "list_files_recursive",
                     "moveto", "copyto", "rmdirs_if_empty", "list_top_level_files", "read_small_text_file", "delete_file",
                     "file_size", "delete_empty_file", "purge_empty_zip_files"):
            setattr(rclone_client, name, getattr(self, name))
        return self
