"""speech 모듈 단위 테스트. Polly/S3 클라이언트를 모킹."""
import io
import sys
from pathlib import Path
from unittest import mock

import pytest

COMMON_DIR = Path(__file__).resolve().parent.parent / "common"
sys.path.insert(0, str(COMMON_DIR))

from speech import SpeechError, SpeechSynthesizer  # noqa: E402


class FakePolly:
    def __init__(self, audio=b"ID3mp3bytes", exc=None):
        self._audio = audio
        self._exc = exc
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return {"AudioStream": io.BytesIO(self._audio)}


class FakeS3:
    def __init__(self):
        self.put_calls = []
        self.presign_calls = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        self.presign_calls.append((op, Params, ExpiresIn))
        return f"https://example.s3/{Params['Key']}?exp={ExpiresIn}"


def _synth(**kw):
    return SpeechSynthesizer(
        bucket="signmed-audio", region="ap-northeast-2",
        polly=kw.get("polly", FakePolly()), s3=kw.get("s3", FakeS3()),
    )


def test_synthesize_mp3_바이트():
    s = _synth()
    audio = s.synthesize("저 병원에 가려고요")
    assert audio == b"ID3mp3bytes"
    kw = s._polly.calls[0]
    assert kw["OutputFormat"] == "mp3"
    assert kw["VoiceId"] == "Seoyeon"
    assert kw["LanguageCode"] == "ko-KR"


def test_key_포맷():
    s = _synth()
    assert s.key_for("abc123", ts=42) == "audio/abc123/42.mp3"


def test_synthesize_to_url_전체흐름():
    polly, s3 = FakePolly(), FakeS3()
    s = _synth(polly=polly, s3=s3)
    url = s.synthesize_to_url("안녕하세요", session_id="abc123", ts=100)
    # 업로드가 올바른 버킷/키/컨텐츠타입으로 됐는지
    put = s3.put_calls[0]
    assert put["Bucket"] == "signmed-audio"
    assert put["Key"] == "audio/abc123/100.mp3"
    assert put["ContentType"] == "audio/mpeg"
    # presigned URL 반환
    assert url == "https://example.s3/audio/abc123/100.mp3?exp=300"


def test_bucket_없으면_업로드_에러():
    s = SpeechSynthesizer(bucket=None, polly=FakePolly(), s3=FakeS3())
    with pytest.raises(SpeechError):
        s.upload(b"x", "audio/a/1.mp3")


def test_polly_ClientError면_SpeechError():
    from botocore.exceptions import ClientError

    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}},
                      "SynthesizeSpeech")
    s = _synth(polly=FakePolly(exc=err))
    with pytest.raises(SpeechError):
        s.synthesize("x")


def test_AudioStream_없으면_SpeechError():
    polly = FakePolly()
    polly.synthesize_speech = lambda **kw: {}  # AudioStream 없음
    s = _synth(polly=polly)
    with pytest.raises(SpeechError):
        s.synthesize("x")


def test_ttl_환경변수_반영(monkeypatch):
    monkeypatch.setenv("URL_TTL_SEC", "60")
    s = SpeechSynthesizer(bucket="b", polly=FakePolly(), s3=FakeS3())
    assert s.ttl == 60
