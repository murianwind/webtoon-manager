"""앱 코드 위생 검사 — 소스(app/)를 훑어서 다음 실수가 다시 들어오지 않게 한다.

1. except 안에서 새 예외를 던질 때는 `from e`(또는 의도적으로 `from None`)로 원인을 이어 둔다 — 안 그러면 "처리 중 다른 예외가 발생했습니다"로 보여서 원래 원인을 찾기 어렵다.
2. 넓은 except(Exception/BaseException/맨 except)가 로그 없이 pass/continue/return None으로 삼키지 않는다 — 실패가 흔적 없이 사라진다. 지킬 이유가 있으면 좁은 예외만 받거나 로그를 남긴다.
3. 쓰이지 않는 인자가 있는 함수(데코레이터 없는 모듈 함수)가 없다 — 호출부가 쓸모없는 값을 계속 넘기게 된다.
4. 보안용이 아니어도 SHA1/MD5 같은 약한 해시를 쓰지 않는다(변경 감지용이어도 한 가지로 통일).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "app").rglob("*.py"))
assert len(FILES) > 30, len(FILES)
problems = []


def rel(f, line): return f"{f.relative_to(ROOT)}:{line}"


def is_log_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ("log", "logging", "logger")


for f in FILES:
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        # 1) except 안에서 원인을 잇지 않고 새 예외를 던짐(그대로 다시 던지는 `raise e`/`raise`는 제외)
        for sub in ast.walk(handler):
            if isinstance(sub, ast.Raise) and sub.exc is not None and sub.cause is None and not (isinstance(sub.exc, ast.Name) and handler.name and sub.exc.id == handler.name):
                problems.append(f"{rel(f, sub.lineno)} except 안에서 원인을 잇지 않고 새 예외를 던짐(`from e`를 붙이세요): {ast.unparse(sub.exc)[:60]}")
        # 2) 넓은 except가 로그 없이 조용히 삼킴
        broad = handler.type is None or ast.unparse(handler.type) in ("Exception", "BaseException")
        if broad:
            body = [b for b in handler.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
            only_silent = all(isinstance(b, (ast.Pass, ast.Continue)) or (isinstance(b, ast.Return) and (b.value is None or (isinstance(b.value, ast.Constant) and b.value.value is None))) for b in body)
            logs = any(is_log_call(n) for b in handler.body for n in ast.walk(b))
            if only_silent and not logs:
                problems.append(f"{rel(f, handler.lineno)} 넓은 except가 로그 없이 삼킴(좁은 예외만 받거나 log.warning을 남기세요)")
    # 3) 쓰이지 않는 인자(데코레이터 없는 모듈 함수)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list:
            if all(isinstance(b, ast.Pass) or (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)) for b in node.body):
                continue  # 껍데기 함수
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            for a in node.args.args + node.args.kwonlyargs:
                if not a.arg.startswith("_") and a.arg not in used:
                    problems.append(f"{rel(f, node.lineno)} {node.name}()의 인자 `{a.arg}`가 쓰이지 않음(지우고 호출부도 고치세요)")
    # 4) 약한 해시
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        name = ast.unparse(call.func)
        if name in ("hashlib.sha1", "hashlib.md5") or (name == "hashlib.new" and call.args and isinstance(call.args[0], ast.Constant) and str(call.args[0].value).lower() in ("sha1", "md5")):
            problems.append(f"{rel(f, call.lineno)} 약한 해시 {name} (sha256을 쓰세요)")

assert not problems, "\n" + "\n".join(problems)
print(f"앱 코드 위생 OK ({len(FILES)}개 파일 검사)")
print("\n전부 통과")
