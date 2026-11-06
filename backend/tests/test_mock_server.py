"""Mock WebSocket 서버 라우팅 로직 단위 테스트(websockets 불필요)."""
import json
import sys
from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parent.parent / "mock"
sys.path.insert(0, str(MOCK_DIR))

from server import handle_message  # noqa: E402


def test_recognize_누적():
    buf = {}
    r1 = handle_message(
        json.dumps({"action": "recognize", "sessionId": "s", "keypoints": [[[0, 0]]]}),
        buffers=buf,
    )
    assert r1["type"] == "word"
    assert r1["count"] == 1
    assert r1["words"] == [r1["word"]]
    assert r1["candidates"][0]["name"] == r1["word"]

    r2 = handle_message(
        json.dumps({"action": "recognize", "sessionId": "s", "keypoints": [[[0, 0]]]}),
        buffers=buf,
    )
    assert r2["count"] == 2
    assert len(r2["words"]) == 2


def test_recognize_keypoints_없으면_error():
    r = handle_message({"action": "recognize", "sessionId": "s"}, buffers={})
    assert r["type"] == "error"
    assert r["stage"] == "recognize"


def test_finalize_버퍼사용_후_비움():
    buf = {"s": ["나", "병원"]}
    r = handle_message({"action": "finalize", "sessionId": "s"}, buffers=buf)
    assert r["type"] == "speech"
    assert r["words"] == ["나", "병원"]
    assert r["sentence"]
    assert "audioUrl" in r and "needsConfirm" in r
    assert buf["s"] == []  # 비워짐


def test_finalize_body_words_우선():
    buf = {"s": ["무시"]}
    r = handle_message({"action": "finalize", "sessionId": "s", "words": ["맞다"]}, buffers=buf)
    assert r["words"] == ["맞다"]


def test_finalize_빈_버퍼면_error():
    r = handle_message({"action": "finalize", "sessionId": "s"}, buffers={})
    assert r["type"] == "error"
    assert r["stage"] == "sentence"


def test_doctorUtterance_transcript():
    r = handle_message({"action": "doctorUtterance", "sessionId": "s", "text": "어디가 아프세요?"})
    assert r == {"type": "transcript", "text": "어디가 아프세요?"}


def test_doctorUtterance_빈_텍스트_error():
    r = handle_message({"action": "doctorUtterance", "sessionId": "s", "text": "  "})
    assert r["type"] == "error"
    assert r["stage"] == "transcript"


def test_알수없는_action():
    r = handle_message({"action": "nope", "sessionId": "s"}, buffers={})
    assert r["type"] == "error"
    assert r["retryable"] is False


def test_잘못된_json():
    r = handle_message("{not json", buffers={})
    assert r["type"] == "error"


def test_세션별_버퍼_분리():
    buf = {}
    handle_message({"action": "recognize", "sessionId": "a", "keypoints": [1]}, buffers=buf)
    handle_message({"action": "recognize", "sessionId": "b", "keypoints": [1]}, buffers=buf)
    handle_message({"action": "recognize", "sessionId": "a", "keypoints": [1]}, buffers=buf)
    assert buf["a"] and buf["b"]
    assert len(buf["a"]) == 2
    assert len(buf["b"]) == 1
