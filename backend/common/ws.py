"""WebSocket 공통 유틸. recognize/finalize Lambda가 함께 쓴다.

API Gateway WebSocket에서
  - connectionId 는 event["requestContext"]["connectionId"]
  - 응답은 apigatewaymanagementapi 의 post_to_connection 으로 보낸다
    (엔드포인트는 domainName/stage 로 구성).

사용:
    from ws import connection_id, make_poster, send
    cid = connection_id(event)
    post = make_poster(event)          # boto3 apigatewaymanagementapi 클라이언트
    send(post, cid, {"type": "word", "word": "병원"})
"""
import json
import os


def connection_id(event):
    return event.get("requestContext", {}).get("connectionId")


def _endpoint_url(event):
    rc = event.get("requestContext", {})
    domain = rc.get("domainName")
    stage = rc.get("stage")
    # 로컬/테스트에서 requestContext가 없으면 환경변수로 대체
    if not domain or not stage:
        return os.environ.get("WS_ENDPOINT")
    return f"https://{domain}/{stage}"


def make_poster(event, client=None, region=None):
    """post_to_connection 가능한 클라이언트를 만든다. 주입하면 그대로 쓴다."""
    if client is not None:
        return client
    import boto3

    region = region or os.environ.get("AWS_REGION")
    return boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=_endpoint_url(event),
        region_name=region,
    )


def send(poster, connection_id, payload):
    """payload(dict)를 JSON으로 직렬화해 연결로 보낸다."""
    poster.post_to_connection(
        ConnectionId=connection_id,
        Data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def parse_body(event):
    """WebSocket 프레임의 body(JSON 문자열)를 dict로 파싱한다."""
    body = event.get("body")
    if body is None:
        return {}
    if isinstance(body, (dict, list)):
        return body
    return json.loads(body)
