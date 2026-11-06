"""문장 → 음성(mp3) → S3 저장 → presigned URL 공용 모듈.

흐름 ①의 finalize Lambda가 쓴다.
Polly로 한국어 문장을 mp3로 합성하고, S3에 올린 뒤 임시 GET URL을 돌려준다.
음성 파일을 WebSocket으로 직접 보내지 않고 URL로 전달한다(메시지 크기 제한 회피).

사용:
    from speech import SpeechSynthesizer
    s = SpeechSynthesizer(bucket="signmed-audio", region="ap-northeast-2")
    url = s.synthesize_to_url("저 병원에 가려고요", session_id="abc123")

환경 변수(기본값)
  AUDIO_BUCKET   mp3를 올릴 S3 버킷
  POLLY_VOICE    Polly 보이스 (기본 Seoyeon, 한국어)
  AWS_REGION     리전 (기본 ap-northeast-2)
  URL_TTL_SEC    presigned URL 만료 초 (기본 300)
"""
import os
import time

DEFAULT_REGION = "ap-northeast-2"
DEFAULT_VOICE = "Seoyeon"
DEFAULT_TTL = 300


class SpeechError(Exception):
    """음성 합성/업로드 실패."""


def _clients(region):
    import boto3

    return boto3.client("polly", region_name=region), boto3.client("s3", region_name=region)


class SpeechSynthesizer:
    def __init__(self, bucket=None, region=None, voice=None, ttl=None,
                 polly=None, s3=None):
        self.bucket = bucket or os.environ.get("AUDIO_BUCKET")
        self.region = region or os.environ.get("AWS_REGION", DEFAULT_REGION)
        self.voice = voice or os.environ.get("POLLY_VOICE", DEFAULT_VOICE)
        self.ttl = ttl if ttl is not None else int(os.environ.get("URL_TTL_SEC", DEFAULT_TTL))
        # 클라이언트 주입 시 boto3 없이 테스트 가능
        self._polly = polly
        self._s3 = s3

    def _ensure_clients(self):
        if self._polly is None or self._s3 is None:
            polly, s3 = _clients(self.region)
            self._polly = self._polly or polly
            self._s3 = self._s3 or s3

    def synthesize(self, text):
        """문장 → mp3 바이트."""
        from botocore.exceptions import ClientError

        self._ensure_clients()
        try:
            r = self._polly.synthesize_speech(
                Text=text,
                OutputFormat="mp3",
                VoiceId=self.voice,
                LanguageCode="ko-KR",
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise SpeechError(f"Polly 합성 실패: {code}") from e
        stream = r.get("AudioStream")
        if stream is None:
            raise SpeechError("Polly 응답에 AudioStream 없음")
        return stream.read()

    def key_for(self, session_id, ts=None):
        ts = ts if ts is not None else int(time.time() * 1000)
        return f"audio/{session_id}/{ts}.mp3"

    def upload(self, audio_bytes, key):
        """mp3 바이트를 S3에 올린다."""
        from botocore.exceptions import ClientError

        if not self.bucket:
            raise SpeechError("AUDIO_BUCKET이 설정되지 않음")
        self._ensure_clients()
        try:
            self._s3.put_object(
                Bucket=self.bucket, Key=key, Body=audio_bytes, ContentType="audio/mpeg"
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise SpeechError(f"S3 업로드 실패: {code}") from e

    def presign(self, key):
        """key에 대한 임시 GET URL."""
        self._ensure_clients()
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.ttl,
        )

    def synthesize_to_url(self, text, session_id, ts=None):
        """문장 → mp3 → S3 → presigned URL (한 번에)."""
        audio = self.synthesize(text)
        key = self.key_for(session_id, ts)
        self.upload(audio, key)
        return self.presign(key)
