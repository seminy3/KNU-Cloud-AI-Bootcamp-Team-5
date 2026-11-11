"""
팀 데이터셋(최종 폴더)의 클립으로 모델을 채점한다. keypoints/<clip_id>.npy (30, 49, 2)를 요청 형식(keypoints)으로 넣고 label_id 로 비교한다.

  python deploy\\eval_team.py --model_dir ..\\holdout_cnn                    # 팀 val (aihub02, aihub06) 만
  python deploy\\eval_team.py --model_dir ..\\final_model_cnn_spec2d_c --all  # 팀 데이터 전체를 구간별로

팀 val 수어자는 우리 데이터의 REAL02, REAL06 과 같은 영상이다. 새 수어자 정확도를 보려면 이 둘을 빼고 학습한 모델
(train_final_2d.py --holdout REAL02,REAL06 --save --out_dir ...)로 채점해야 하고, 전체 수어자로 학습한 모델로 채점하면 학습에 쓴 수어자가 들어간다.
"""
import argparse
import collections
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))
import inference  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model_dir', required=True)
    ap.add_argument('--team_dir', default=os.path.join(HERE, '..', '..', '..', '최종'))
    ap.add_argument('--all', action='store_true', help="val 만이 아니라 팀 데이터 전체를 구간별로 채점")
    args = ap.parse_args()
    model = inference.model_fn(args.model_dir)
    with open(os.path.join(args.team_dir, 'index.csv'), encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    ai_ids = {int(r['label_id']) for r in rows if r['source'] == 'aihub'}
    nums = {str(i) for i in range(1, 11)}

    def groups(r):
        if args.all:
            if r['signer'] == 'sldict':
                return ['수어사전 ' + ('원본' if r['source'] == 'sldict' else '증강')]
            if r['source'] == 'aihub':
                return ['AI Hub 원본 ' + ('val 수어자' if r['split'] == 'val' else 'train 수어자')]
            return ['AI Hub 증강']
        if r['split'] != 'val':
            return []
        return ['전체', '숫자 1~10' if r['label'] in nums else '진료 76', r['signer']]

    st = collections.defaultdict(lambda: [0, 0, 0, 0])
    bad = []
    for r in rows:
        gs = groups(r)
        if not gs:
            continue
        x = np.load(os.path.join(args.team_dir, 'keypoints', r['clip_id'] + '.npy')).astype(np.float32)
        p = inference.predict_fn(inference.input_fn(json.dumps({'keypoints': x.tolist(), 'top_k': 178})), model)
        ids = [t['label_id'] for t in p['top_k']]
        y = int(r['label_id'])
        top86 = [i for i in ids if i in ai_ids][0]
        for g in gs:
            a = st[g]
            a[0] += 1
            a[1] += ids[0] == y
            a[2] += y in ids[:5]
            a[3] += top86 == y
        if ids[0] != y and not args.all:
            bad.append((r['clip_id'], r['label'], p['top_k'][0]['label'], round(p['top_k'][0]['prob'], 2)))
    for g, (n, a, b, c) in st.items():
        print(f"{g:22s} n={n:4d}  1위 {a / n * 100:6.2f}%  상위5 {b / n * 100:6.2f}%  1위(AI Hub 86 후보만) {c / n * 100:6.2f}%")
    for b in bad:
        print('  틀림:', b)


if __name__ == '__main__':
    main()
