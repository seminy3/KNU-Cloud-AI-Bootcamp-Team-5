"""
무작위 가중치의 '더미' 모델을 같은 형식(model.pt + meta.json)으로 만든다.
최종 모델이 나오기 전에 SageMaker 쪽 권한·할당량·컨테이너·엔드포인트 호출을 미리 점검하는 용도다. 예측 내용은 의미가 없다.

  python deploy\\make_dummy_model.py --out_dir ..\\dummy_model --arch cnn --classes 99
  python deploy\\make_model_tar.py --model_dir ..\\dummy_model --out ..\\dummy_model.tar.gz
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))

import torch

import inference as inf
import sign_features as sf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out_dir', required=True)
    ap.add_argument('--arch', choices=['cnn', 'gru'], default='cnn')
    ap.add_argument('--classes', type=int, default=99)
    ap.add_argument('--hidden', type=int, default=None, help="기본: cnn 512, gru 384 (최종 설정과 같은 크기)")
    args = ap.parse_args()
    hidden = args.hidden or (512 if args.arch == 'cnn' else 384)
    meta = {'arch': args.arch, 'hidden': hidden, 'dropout': 0.0, 'classes': [f'더미{i}' for i in range(args.classes)],
            'word_ids': [None] * args.classes, 'columns': sf.model_columns(), 'n_feat': 183, 'frames': 30,
            'mean': [0.0] * 183, 'std': [1.0] * 183, 'dummy': True}
    torch.manual_seed(0)
    net = inf.build_net(meta)
    os.makedirs(args.out_dir, exist_ok=True)
    torch.save({k: v.contiguous() for k, v in net.state_dict().items()}, os.path.join(args.out_dir, 'model.pt'))
    with open(os.path.join(args.out_dir, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"더미 모델 저장: {os.path.abspath(args.out_dir)} ({args.arch}, hidden {hidden}, 클래스 {args.classes})")


if __name__ == '__main__':
    main()
