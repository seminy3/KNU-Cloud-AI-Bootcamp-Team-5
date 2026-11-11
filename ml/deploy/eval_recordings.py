"""
웹캠 테스트 페이지(webcam_test.py)가 저장한 녹화(webcam\\recordings\\*.json)를 모델에 넣어, 녹화에 적힌 단어가 몇 위로 나오는지 센다.
학습에 쓰지 않은 사람이 한 수어로 모델을 확인하는 용도다. 녹화에 적힌 라벨('<단어id> <이름>')의 이름을 정답으로 본다.

  python deploy\\eval_recordings.py --model_dir ..\\final_model_cnn_spec2d_b --dir deploy\\webcam\\recordings --since 20261104_1846

--since: 파일 이름이 이 값 이상인 녹화만 쓴다 (기본은 전부).
라벨이 비어 있는 녹화(수어를 하지 않은 입력)는 정답이 없으므로 센다에서 뺀다.
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))
import inference  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model_dir', required=True)
    ap.add_argument('--dir', default=os.path.join(HERE, 'webcam', 'recordings'))
    ap.add_argument('--since', default='')
    args = ap.parse_args()
    model = inference.model_fn(args.model_dir)
    n = ok1 = ok5 = 0
    for f in sorted(glob.glob(os.path.join(args.dir, '*.json'))):
        base = os.path.basename(f)
        if base < args.since:
            continue
        d = json.load(open(f, encoding='utf-8'))
        if not d.get('label', '').strip():
            continue
        name = re.sub(r'^\d+\s*', '', d.get('label', '')).strip()
        pl = dict(d['payload'])
        pl['top_k'] = 178
        p = inference.predict_fn(inference.input_fn(json.dumps(pl)), model)
        if not p.get('ok'):
            print(f"{base[:15]} {d.get('label')}: 판정 불가 ({p.get('reason')})")
            continue
        names = [t['name'] for t in p['top_k']]
        rank = names.index(name) + 1 if name in names else None
        n += 1
        ok1 += rank == 1
        ok5 += rank is not None and rank <= 5
        top = p['top_k'][0]
        print(f"{base[:15]} 정답 {d.get('label'):10s} 순위 {rank if rank else '-':>3}  1위 {top['name']} {top['prob']:.2f}  정답 확률 "
              f"{p['top_k'][rank - 1]['prob']:.3f}" if rank else f"{base[:15]} 정답 {d.get('label')}: 후보에 없음")
    if n:
        print(f"\n{n}개: 1위 {ok1}개 ({ok1 / n * 100:.0f}%), 상위 5 {ok5}개 ({ok5 / n * 100:.0f}%)")


if __name__ == '__main__':
    main()
