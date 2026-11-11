"""
SageMaker 에 올릴 model.tar.gz 를 만든다.

  python deploy\\make_model_tar.py --model_dir ..\\final_model_cnn --out ..\\final_model_cnn.tar.gz

model_dir 에는 train_final.py 가 만든 model.pt, meta.json 이 있어야 한다.
tar 안 구조: model.pt, meta.json, code/inference.py, code/sign_features.py, code/spec2d.py  (Windows 경로 구분자 없이 '/' 만 사용)
만든 뒤 임시 폴더에 풀어 model_fn 으로 다시 읽어 보는 검사를 한다 (torch 가 있을 때).
"""
import argparse
import os
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, 'code')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model_dir', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    for f in ('model.pt', 'meta.json'):
        if not os.path.isfile(os.path.join(args.model_dir, f)):
            raise SystemExit(f"{f} 가 없습니다: {args.model_dir}")
    items = [(os.path.join(args.model_dir, 'model.pt'), 'model.pt'),
             (os.path.join(args.model_dir, 'meta.json'), 'meta.json'),
             (os.path.join(CODE, 'inference.py'), 'code/inference.py'),
             (os.path.join(CODE, 'sign_features.py'), 'code/sign_features.py'),
             (os.path.join(CODE, 'spec2d.py'), 'code/spec2d.py')]
    with tarfile.open(args.out, 'w:gz') as tar:
        for src, arc in items:
            tar.add(src, arcname=arc)
    print(f"만듦: {os.path.abspath(args.out)} ({os.path.getsize(args.out) / 1e6:.2f} MB)")
    with tarfile.open(args.out) as tar:
        print("  내용:", ', '.join(tar.getnames()))
        try:
            import torch  # noqa: F401
        except Exception:
            print("  (torch 가 없어 다시 읽어 보는 검사는 건너뜀)")
            return
        with tempfile.TemporaryDirectory() as d:
            try:
                tar.extractall(d, filter='data')
            except TypeError:          # 오래된 파이썬은 filter 인자가 없음
                tar.extractall(d)
            sys.path.insert(0, os.path.join(d, 'code'))
            import inference
            m = inference.model_fn(d)
            print(f"  다시 읽기 검사 통과: {m['meta']['arch']}, 클래스 {len(m['meta']['classes'])}개")


if __name__ == '__main__':
    main()
