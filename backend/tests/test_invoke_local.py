"""invoke_endpoint.py 단위 테스트.

실제 AWS 없이 가짜 클라이언트를 주입해 판정 로직/실패 처리/자격증명 없음 경로를 확인한다.

    python -m pytest backend/tests/
"""
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import invoke_endpoint as mod  # noqa: E402


class FakeClient:
    """invoke_endpoint 호출마다 미리 넣어둔 응답(dict)이나 예외를 돌려준다."""

    def __init__(self, responses):
        # responses: list of dict (성공 응답) 또는 Exception 인스턴스
        self._responses = list(responses)
        self.calls = []

    def invoke_endpoint(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"Body": io.BytesIO(json.dumps(item).encode("utf-8"))}


def _result(name, prob):
    return {"ok": True, "top_k": [{"name": name, "prob": prob}]}


def test_모든_케이스_정답이면_통과(monkeypatch):
    # CASES 순서(아프다/설사/상처/다치다)에 맞춰 로컬 기대 확률과 똑같이 응답
    client = FakeClient([
        _result("아프다", 0.603),
        _result("설사", 0.547),
        _result("상처/다치다", 0.607),
    ])
    ok = mod.run("sign-word-model", "ap-northeast-2", tol=0.01, client=client)
    assert ok is True
    # 세 번 호출됐고 ContentType이 json인지
    assert len(client.calls) == 3
    assert client.calls[0]["ContentType"] == "application/json"


def test_1위_이름_불일치면_실패(monkeypatch):
    client = FakeClient([
        _result("감기", 0.603),   # 틀린 이름
        _result("설사", 0.547),
        _result("상처/다치다", 0.607),
    ])
    ok = mod.run("sign-word-model", "ap-northeast-2", tol=0.01, client=client)
    assert ok is False


def test_확률_허용오차_초과하면_실패(monkeypatch):
    client = FakeClient([
        _result("아프다", 0.90),   # 이름은 맞지만 확률이 기대(0.603)와 큼
        _result("설사", 0.547),
        _result("상처/다치다", 0.607),
    ])
    ok = mod.run("sign-word-model", "ap-northeast-2", tol=0.01, client=client)
    assert ok is False


def test_확률_오차_안이면_통과():
    # 기대 0.603 ± 0.01 안
    client = FakeClient([
        _result("아프다", 0.61),
        _result("설사", 0.55),
        _result("상처/다치다", 0.60),
    ])
    ok = mod.run("sign-word-model", "ap-northeast-2", tol=0.01, client=client)
    assert ok is True


def test_자격증명_없으면_None_반환하고_크래시_안함():
    from botocore.exceptions import NoCredentialsError

    client = FakeClient([NoCredentialsError()])
    out = mod.run("sign-word-model", "ap-northeast-2", tol=0.01, client=client)
    assert out is None  # 판정 불가(실패 아님)


def test_main은_자격증명없음_경로에서_0으로_종료():
    # build_client가 자격증명 에러를 던지는 상황을 흉내
    from botocore.exceptions import NoCredentialsError

    with mock.patch.object(mod, "build_client", side_effect=NoCredentialsError()):
        code = mod.main(["--endpoint", "sign-word-model"])
    assert code == 0


def test_top1_빈_응답_처리():
    assert mod.top1({"top_k": []}) == (None, None)
    assert mod.top1({}) == (None, None)


def test_judge_정답과_근사확률():
    case = {"file": "test_1.json", "answer": "아프다", "local_prob": 0.603}
    ok, fails = mod.judge(case, _result("아프다", 0.60), tol=0.01)
    assert ok is True and fails == []
