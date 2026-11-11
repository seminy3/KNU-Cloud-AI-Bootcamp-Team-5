"""
1위 확률(prob)을 임계값으로 잘랐을 때 어떻게 되는지 잰다. 임계값마다 통과한 수, 버려진 맞은 답 수, 통과한 틀린 답 수를 표로 낸다.

  python deploy\\eval_threshold.py --holdout_dir ..\\holdout_cnn --final_dir ..\\final_model_cnn_spec2d_c --team_dir ..\\..\\최종 --recordings deploy\\webcam\\recordings --since 20261104_1846

채점 대상
  A. 팀 val (aihub02, aihub06): holdout_dir 의 모델로 채점한다. 이 두 수어자를 빼고 학습한 모델이라 새 수어자 기준이다.
  B. 웹캠 녹화: final_dir 의 최종 모델로 채점한다. 녹화에 적힌 단어 이름이 정답이다.
  C. 팀 val 에 무작위 구간 경계 오차(spec2d.random_boundary, seed 0)를 준 입력: holdout_dir 의 모델로 채점한다.
  --since 보다 파일 이름이 작은 녹화는 쓰지 않는다. 라벨이 비어 있는 녹화(수어를 하지 않은 입력)도 정답이 없으므로 쓰지 않는다.
  B 가 필요 없으면 --recordings 를 생략한다.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))
import inference  # noqa: E402
import spec2d  # noqa: E402

THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]


def top1(model, payload):
    p = inference.predict_fn(inference.input_fn(json.dumps(payload)), model)
    return p['top_k'][0] if p.get('ok') else None


def report(title, items):
    """items: [(맞았는지, 1위 확률, 수어사전 전용 단어인지)]"""
    ok = np.array([i[0] for i in items])
    prob = np.array([i[1] for i in items])
    print(f"\n== {title}: {len(items)}개, 1위가 맞은 것 {int(ok.sum())}개")
    print(f"   맞은 답의 1위 확률 {prob[ok].min():.2f}~{prob[ok].max():.2f} (중앙값 {np.median(prob[ok]):.2f})")
    if (~ok).any():
        print(f"   틀린 답의 1위 확률 {prob[~ok].min():.2f}~{prob[~ok].max():.2f} (중앙값 {np.median(prob[~ok]):.2f}), 틀린 답 {int((~ok).sum())}개 중 수어사전 전용 단어 {sum(1 for i in items if not i[0] and i[2])}개")
    print("   임계값  통과  버려진 맞은 답  통과한 틀린 답  통과분 정확도")
    for t in THRESHOLDS:
        keep = prob >= t
        n = int(keep.sum())
        acc = f"{(ok & keep).sum() / n * 100:6.1f}%" if n else "     -"
        print(f"   {t:.2f}   {n:4d}   {int((ok & ~keep).sum()):6d}          {int((~ok & keep).sum()):6d}        {acc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--holdout_dir', required=True, help="REAL02, REAL06 을 빼고 학습한 모델 폴더")
    ap.add_argument('--final_dir', default='', help="웹캠 녹화 채점에 쓸 최종 모델 폴더")
    ap.add_argument('--team_dir', default=os.path.join(HERE, '..', '..', '최종'))
    ap.add_argument('--recordings', default='')
    ap.add_argument('--since', default='')
    args = ap.parse_args()

    with open(os.path.join(args.team_dir, 'index.csv'), encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    ai_ids = {int(r['label_id']) for r in rows if r['source'] == 'aihub'}
    name2id = {}
    for r in rows:
        name2id.setdefault(r['label'], int(r['label_id']))

    val = [r for r in rows if r['split'] == 'val']
    X = np.stack([np.load(os.path.join(args.team_dir, 'keypoints', r['clip_id'] + '.npy')).astype(np.float32) for r in val])
    y = [int(r['label_id']) for r in val]
    hold = inference.model_fn(args.holdout_dir)
    Xb = spec2d.random_boundary(X, np.random.default_rng(0))
    for title, XX in (("A. 팀 val (새 수어자, holdout 모델)", X), ("C. 팀 val + 무작위 구간 경계 오차 (holdout 모델)", Xb)):
        items = []
        for x, yy in zip(XX, y):
            t = top1(hold, {'keypoints': x.tolist(), 'top_k': 1})
            items.append((t['label_id'] == yy, t['prob'], False))
        report(title, items)

    if args.recordings and args.final_dir:
        final = inference.model_fn(args.final_dir)
        items = []
        for f in sorted(glob.glob(os.path.join(args.recordings, '*.json'))):
            if os.path.basename(f) < args.since:
                continue
            d = json.load(open(f, encoding='utf-8'))
            if not d.get('label', '').strip():
                continue
            name = re.sub(r'^\d+\s*', '', d.get('label', '')).strip()
            pl = dict(d['payload'])
            pl['top_k'] = 1
            t = top1(final, pl)
            if t is None:
                continue
            lid = name2id.get(name)
            items.append((t['name'] == name, t['prob'], lid is not None and lid not in ai_ids))
        report("B. 웹캠 녹화 (최종 모델)", items)


if __name__ == '__main__':
    main()
