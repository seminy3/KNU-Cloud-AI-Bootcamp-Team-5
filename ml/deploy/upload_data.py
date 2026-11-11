"""
좌표 CSV 폴더들을 세트별 tar.gz 로 묶어 S3 에 올린다 (boto3, 사용자의 AWS 설정을 그대로 사용).

  python deploy\\upload_data.py --bucket <버킷> --prefix sign-data --dry_run      # 크기만 계산
  python deploy\\upload_data.py --bucket <버킷> --prefix sign-data

기본으로 올리는 것 (프로젝트 루트 기준)
  gesture_dataset_final_ar, _mild, _medium, _sa1, _sa2, _severe  -> <prefix>/final_ar<접미사>.tar.gz
  words_final99.txt                                              -> <prefix>/words_final99.txt
올리기 전에 버킷의 '퍼블릭 액세스 차단'이 모두 켜져 있는지 확인하고, 아니면 중단한다
(AI Hub 데이터는 학습·연구용이고 제3자 제공이 안 되므로 비공개 버킷에만 올린다). 서버 측 암호화(AES256)를 지정한다.
올리는 데이터가 좌표 CSV 인지는 확인된 사항이 아니라 가정이다. 팀이 정한 대상이 다르면 --dirs 로 바꾼다.
"""
import argparse
import os
import sys
import tarfile
import tempfile

SUFFIXES = ['', '_mild', '_medium', '_sa1', '_sa2', '_severe']


def pack(src, dst):
    n = 0
    with tarfile.open(dst, 'w:gz') as tar:
        for fn in sorted(os.listdir(src)):
            if fn.lower().endswith('.csv'):
                tar.add(os.path.join(src, fn), arcname=fn)
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--prefix', default='sign-data')
    ap.add_argument('--root', default='..', help="gesture_dataset_final_ar 등이 있는 폴더 (기본: virtual-touch-clickML 의 상위)")
    ap.add_argument('--dirs', default='', help="쉼표로 구분한 폴더 이름 직접 지정 (기본: final_ar 6세트)")
    ap.add_argument('--region', default=None)
    ap.add_argument('--dry_run', action='store_true')
    ap.add_argument('--skip_public_check', action='store_true', help="퍼블릭 액세스 차단 확인을 건너뜀 (권장하지 않음)")
    args = ap.parse_args()

    names = [x.strip() for x in args.dirs.split(',') if x.strip()] or ['gesture_dataset_final_ar' + s for s in SUFFIXES]
    dirs = [(n, os.path.join(args.root, n)) for n in names]
    for n, p in dirs:
        if not os.path.isdir(p):
            raise SystemExit(f"폴더가 없습니다: {os.path.abspath(p)}")

    s3 = None
    if not args.dry_run:
        import boto3
        sess = boto3.session.Session(region_name=args.region) if args.region else boto3.session.Session()
        s3 = sess.client('s3')
        if not args.skip_public_check:
            try:
                cfg = s3.get_public_access_block(Bucket=args.bucket)['PublicAccessBlockConfiguration']
            except Exception as e:
                raise SystemExit(f"퍼블릭 액세스 차단 설정을 읽지 못했습니다 ({type(e).__name__}: {e}). 콘솔에서 비공개인지 확인한 뒤 --skip_public_check 로 다시 실행하세요.")
            if not all(cfg.get(k) for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
                raise SystemExit(f"버킷 {args.bucket} 의 퍼블릭 액세스 차단이 모두 켜져 있지 않습니다: {cfg}. 비공개로 만든 뒤 다시 실행하세요.")
            print("버킷 퍼블릭 액세스 차단: 모두 켜짐")

    tot = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, p in dirs:
            key = f"{args.prefix}/{n.replace('gesture_dataset_', '')}.tar.gz"
            tmp_tar = os.path.join(tmp, os.path.basename(key))
            cnt = pack(p, tmp_tar)
            size = os.path.getsize(tmp_tar)
            tot += size
            print(f"{n}: CSV {cnt}개 -> {size / 1e6:.1f} MB  s3://{args.bucket}/{key}")
            if not args.dry_run:
                s3.upload_file(tmp_tar, args.bucket, key, ExtraArgs={'ServerSideEncryption': 'AES256'})
            os.remove(tmp_tar)
        w = os.path.join(args.root, 'words_final99.txt')
        if os.path.isfile(w) and not args.dirs:
            print(f"words_final99.txt -> s3://{args.bucket}/{args.prefix}/words_final99.txt")
            if not args.dry_run:
                s3.upload_file(w, args.bucket, f"{args.prefix}/words_final99.txt", ExtraArgs={'ServerSideEncryption': 'AES256'})
    print(f"\n합계 {tot / 1e6:.1f} MB ({'계산만 함' if args.dry_run else '업로드 완료'})")


if __name__ == '__main__':
    main()
