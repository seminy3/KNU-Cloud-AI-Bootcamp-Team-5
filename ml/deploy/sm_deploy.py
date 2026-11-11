"""
SageMaker 실시간 엔드포인트 배포·호출·삭제 (boto3). 자격 증명은 이 스크립트가 다루지 않는다:
사용자의 AWS CLI 설정(aws configure / SSO / 환경변수)을 boto3 가 기본 방식대로 읽는다.

  # 1) 모델 tar 를 S3 에 올림
  python deploy\\sm_deploy.py upload --tar ..\\dummy_model.tar.gz --bucket <버킷> --key models/dummy_model.tar.gz
  # 2) 엔드포인트 생성 (끝날 때까지 기다림, 수 분 걸림)
  python deploy\\sm_deploy.py create --name sign-dummy --s3_uri s3://<버킷>/models/dummy_model.tar.gz --role <실행 역할 ARN>
  # 3) 호출 시험 (CSV 한 개를 원시 좌표 요청으로 바꿔서 보냄)
  python deploy\\sm_deploy.py invoke --name sign-dummy --csv ..\\gesture_dataset_final_ar\\<파일>.csv
  # 4) 삭제 (엔드포인트가 켜져 있는 동안 비용이 계속 나감)
  python deploy\\sm_deploy.py delete --name sign-dummy

옵션
  --region        기본은 사용자의 AWS 설정 리전
  --instance      기본 ml.m5.large (CPU). 모델이 작아(파라미터 수백만) CPU 로 시작하는 것을 가정했다. 실제 지연은 재 봐야 안다.
  --serverless    서버리스 엔드포인트로 생성 (--memory 2048 --max_concurrency 2). 첫 호출 지연(콜드 스타트)이 있다. 이 코드에서는 아직 시험하지 않음.
  --image         추론 컨테이너 이미지. 기본값은 SageMaker Python SDK(image_uris.retrieve)가 ap-northeast-2 에 대해 돌려준 PyTorch 2.6 CPU 추론 이미지의 태그이고,
                  리전만 바꿔 넣는다. 다른 리전에 같은 태그가 있는지, ECR 에 실제로 있는지는 생성 때 확인된다 (없으면 create 단계에서 오류).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DLC_ACCOUNT = '763104351884'
DLC_TAG = '2.6.0-cpu-py312'   # sagemaker.image_uris.retrieve('pytorch', 'ap-northeast-2', '2.6.0', py_version='py312', CPU 추론)이 돌려준 태그


def sess(region):
    import boto3
    return boto3.session.Session(region_name=region) if region else boto3.session.Session()


def default_image(region):
    return f"{DLC_ACCOUNT}.dkr.ecr.{region}.amazonaws.com/pytorch-inference:{DLC_TAG}"


def cmd_upload(a, s):
    s3 = s.client('s3')
    size = os.path.getsize(a.tar)
    print(f"업로드: {a.tar} ({size / 1e6:.2f} MB) -> s3://{a.bucket}/{a.key}")
    s3.upload_file(a.tar, a.bucket, a.key, ExtraArgs={'ServerSideEncryption': 'AES256'})
    print(f"완료: s3://{a.bucket}/{a.key}")


def create_resources(sm, name, s3_uri, role, image, instance, serverless, memory, max_conc):
    sm.create_model(
        ModelName=name,
        ExecutionRoleArn=role,
        PrimaryContainer={'Image': image, 'ModelDataUrl': s3_uri,
                          'Environment': {'SAGEMAKER_PROGRAM': 'inference.py'}},
    )
    variant = {'VariantName': 'AllTraffic', 'ModelName': name}
    if serverless:
        variant['ServerlessConfig'] = {'MemorySizeInMB': memory, 'MaxConcurrency': max_conc}
    else:
        variant.update(InstanceType=instance, InitialInstanceCount=1)
    sm.create_endpoint_config(EndpointConfigName=name, ProductionVariants=[variant])
    sm.create_endpoint(EndpointName=name, EndpointConfigName=name)


def cmd_create(a, s):
    sm = s.client('sagemaker')
    region = s.region_name
    image = a.image or default_image(region)
    print(f"리전 {region}, 이미지 {image}")
    print(f"모델 {a.s3_uri}, 역할 {a.role}, {'서버리스 %dMB' % a.memory if a.serverless else a.instance}")
    create_resources(sm, a.name, a.s3_uri, a.role, image, a.instance, a.serverless, a.memory, a.max_concurrency)
    print("생성 요청 완료. InService 가 될 때까지 기다리는 중 (수 분)...")
    t0 = time.time()
    try:
        sm.get_waiter('endpoint_in_service').wait(EndpointName=a.name, WaiterConfig={'Delay': 20, 'MaxAttempts': 90})
    except Exception as e:
        d = sm.describe_endpoint(EndpointName=a.name)
        print(f"실패/시간 초과: 상태 {d['EndpointStatus']}, 이유: {d.get('FailureReason', '(없음)')}")
        print(f"컨테이너 로그: CloudWatch 로그 그룹 /aws/sagemaker/Endpoints/{a.name}")
        print(f"정리하려면: python deploy\\sm_deploy.py delete --name {a.name}")
        raise SystemExit(1)
    print(f"InService ({time.time() - t0:.0f}초). 삭제는 delete 로: 켜져 있는 동안 비용이 나갑니다.")


def payload_from_csv(path, top_k=5):
    sys.path.insert(0, HERE)
    import test_local as tl
    times, rows = tl.read_csv(path)
    return {'width': 1920, 'height': 1080, 'top_k': top_k,
            'frames': [dict(t=float(tm), **tl.to_raw(r, 1920, 1080)) for tm, r in zip(times, rows)]}


def cmd_invoke(a, s):
    rt = s.client('sagemaker-runtime')
    if a.csv:
        payload = payload_from_csv(a.csv, a.top_k)
    else:
        with open(a.payload, encoding='utf-8') as f:
            payload = json.load(f)
    body = json.dumps(payload).encode('utf-8')
    print(f"요청 {len(body) / 1e3:.1f} KB, 프레임 {len(payload.get('frames', []))}개")
    for i in range(a.repeat):
        t0 = time.time()
        r = rt.invoke_endpoint(EndpointName=a.name, ContentType='application/json', Accept='application/json', Body=body)
        ms = (time.time() - t0) * 1000
        out = json.loads(r['Body'].read().decode('utf-8'))
        if i == 0:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"  호출 {i + 1}/{a.repeat}: {ms:.0f} ms (클라이언트에서 잰 왕복 시간)")


def cmd_delete(a, s):
    sm = s.client('sagemaker')
    for label, fn, kw in (('엔드포인트', sm.delete_endpoint, {'EndpointName': a.name}),
                          ('엔드포인트 설정', sm.delete_endpoint_config, {'EndpointConfigName': a.name}),
                          ('모델', sm.delete_model, {'ModelName': a.name})):
        try:
            fn(**kw)
            print(f"삭제 요청: {label} {a.name}")
        except Exception as e:
            print(f"건너뜀: {label} {a.name} ({type(e).__name__}: {e})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--region', default=None)
    sub = ap.add_subparsers(dest='cmd', required=True)

    u = sub.add_parser('upload')
    u.add_argument('--tar', required=True)
    u.add_argument('--bucket', required=True)
    u.add_argument('--key', required=True)

    c = sub.add_parser('create')
    c.add_argument('--name', required=True)
    c.add_argument('--s3_uri', required=True)
    c.add_argument('--role', required=True)
    c.add_argument('--instance', default='ml.m5.large')
    c.add_argument('--image', default='')
    c.add_argument('--serverless', action='store_true')
    c.add_argument('--memory', type=int, default=2048)
    c.add_argument('--max_concurrency', type=int, default=2)

    i = sub.add_parser('invoke')
    i.add_argument('--name', required=True)
    g = i.add_mutually_exclusive_group(required=True)
    g.add_argument('--csv')
    g.add_argument('--payload')
    i.add_argument('--top_k', type=int, default=5)
    i.add_argument('--repeat', type=int, default=3)

    d = sub.add_parser('delete')
    d.add_argument('--name', required=True)

    a = ap.parse_args()
    s = sess(a.region)
    if not s.region_name:
        raise SystemExit("리전을 알 수 없습니다. --region 을 주거나 aws configure 로 리전을 설정하세요.")
    {'upload': cmd_upload, 'create': cmd_create, 'invoke': cmd_invoke, 'delete': cmd_delete}[a.cmd](a, s)


if __name__ == '__main__':
    main()
