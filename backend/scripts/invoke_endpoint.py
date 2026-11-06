"""SageMaker 엔드포인트(sign-word-model) 로컬 자동 검증 스크립트.

test_1~3.json 을 순서대로 엔드포인트에 호출해
  1. 응답 1위(top_k[0].name)가 정답과 같은지
  2. 1위 확률이 로컬 기대값과 허용 오차 안인지
를 자동 판정하고, 콜드/웜 응답 시간을 출력한다.

CLI 자격증명이 있을 때만 실제 호출한다. 없으면 안내만 하고 정상 종료(0)한다.
검증 실패가 하나라도 있으면 비정상 종료(1) → CI 연동 대비.

    python backend/scripts/invoke_endpoint.py
    python backend/scripts/invoke_endpoint.py --endpoint sign-word-model --region ap-northeast-2 --tol 0.01

규격은 ENDPOINT.md, 기대값 근거는 ENDPOINT_TEST.md 참고.
"""
import argparse
import json
import sys
import time
from pathlib import Path

# 저장소 루트 (이 파일: backend/scripts/invoke_endpoint.py → 루트는 parents[2])
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ENDPOINT = "sign-word-model"
DEFAULT_REGION = "ap-northeast-2"

# 파일 → (정답 이름, 로컬 1위 확률). ENDPOINT_TEST.md 표에서.
CASES = [
    {"file": "test_1.json", "answer": "아프다", "local_prob": 0.603},
    {"file": "test_2.json", "answer": "설사", "local_prob": 0.547},
    {"file": "test_3.json", "answer": "상처/다치다", "local_prob": 0.607},
]


def build_client(region):
    """sagemaker-runtime 클라이언트를 만든다. 테스트에서 모킹하기 쉽게 분리."""
    import boto3

    return boto3.client("sagemaker-runtime", region_name=region)


def invoke(client, endpoint, payload):
    """엔드포인트를 한 번 호출하고 (응답 dict, 소요 ms)를 돌려준다."""
    t = time.time()
    r = client.invoke_endpoint(
        EndpointName=endpoint,
        ContentType="application/json",
        Body=json.dumps(payload),
    )
    body = json.loads(r["Body"].read())
    return body, round((time.time() - t) * 1000)


def top1(result):
    """응답에서 1위 (name, prob)를 뽑는다. 없으면 (None, None)."""
    tk = (result or {}).get("top_k") or []
    if not tk:
        return None, None
    return tk[0].get("name"), tk[0].get("prob")


def judge(case, result, tol):
    """정답 일치 + 확률 근사 판정. (pass 여부, 메시지 목록)."""
    name, prob = top1(result)
    fails = []
    if name != case["answer"]:
        fails.append(f"1위 이름 불일치: 기대 {case['answer']}, 실제 {name}")
    if prob is None:
        fails.append("확률 없음")
    elif abs(prob - case["local_prob"]) > tol:
        fails.append(
            f"확률 차이 큼: 기대 ~{case['local_prob']}, 실제 {prob} (허용 ±{tol})"
        )
    return (not fails), fails


def load_payload(file_name):
    path = REPO_ROOT / file_name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(endpoint, region, tol, client=None):
    """전 케이스를 돌리고 모두 통과하면 True. 클라이언트는 주입 가능(테스트용)."""
    if client is None:
        try:
            client = build_client(region)
        except Exception as e:  # noqa: BLE001
            print(f"[안내] boto3 클라이언트를 만들 수 없습니다: {e}")
            print("       자격증명 없이도 실패가 아닙니다. 자격증명을 설정한 뒤 다시 실행하세요.")
            return None  # 판정 불가(자격증명 없음)

    all_pass = True
    first_elapsed = None
    for i, case in enumerate(CASES):
        payload = load_payload(case["file"])
        try:
            result, ms = invoke(client, endpoint, payload)
        except Exception as e:  # noqa: BLE001
            # 자격증명/권한 문제면 여기서 걸린다. 첫 케이스에서 자격증명류면 안내 후 중단.
            name = type(e).__name__
            if name in ("NoCredentialsError", "CredentialRetrievalError",
                        "PartialCredentialsError"):
                print(f"[안내] AWS 자격증명이 없습니다({name}). 설정 후 다시 실행하세요.")
                return None
            print(f"FAIL {case['file']}: 호출 오류 {name}: {e}")
            all_pass = False
            continue

        if i == 0:
            first_elapsed = ms  # 첫 호출 = 콜드 스타트 가능성

        ok, fails = judge(case, result, tol)
        name, prob = top1(result)
        status = "PASS" if ok else "FAIL"
        extra = f"  [{'; '.join(fails)}]" if fails else ""
        print(f"{status} {case['file']:<12} {ms:>6}ms  1위={name} prob={prob}{extra}")
        all_pass = all_pass and ok

    if first_elapsed is not None:
        print(f"\n첫 호출(콜드 가능) {first_elapsed}ms. 콜드가 길면 시연 전 워밍업 필요.")
    return all_pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="sign-word-model 엔드포인트 자동 검증")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--tol", type=float, default=0.01, help="확률 허용 오차 (기본 0.01)")
    args = ap.parse_args(argv)

    result = run(args.endpoint, args.region, args.tol)
    if result is None:
        # 자격증명 없음 → 실패 아님(크래시 금지). 종료 코드 0.
        return 0
    if result:
        print("\n모든 케이스 통과.")
        return 0
    print("\n실패한 케이스가 있습니다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
