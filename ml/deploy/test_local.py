"""
배포용 전처리·추론 코드를 로컬에서 검증한다 (AWS 불필요).

  python deploy\\test_local.py --csv_dir ..\\gesture_dataset_final_ar [--n 12]

검사 항목
  1) 전처리 동치성: 학습 CSV(보정본) 한 행을 '원시 MediaPipe 좌표'로 역산해 sign_features.frame_vector 에 넣으면 CSV 의 183개 값이 다시 나오는가
  2) 시퀀스 동치성: frames 입력 / rows 입력 / 학습 코드(train_svm.build_features)의 30프레임 시퀀스가 서로 같은가
  3) 모델 클래스 동치성: inference.CNN/GRU 가 train_seq.CNN/GRU 와 같은 state_dict 키·모양·출력을 내는가
  4) 핸들러: 무작위 가중치 모델로 model_fn -> input_fn -> predict_fn -> output_fn 이 돌고 응답 형식이 맞는가
     (무작위 가중치라 예측 내용은 의미 없음. 입출력 연결만 확인)
실제 MediaPipe 가 만든 좌표로 하는 검증은 아님. 그것은 웹캠/영상으로 따로 해야 한다 (INPUT_SPEC.md 의 '남은 확인').
"""
import argparse
import glob
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np

import sign_features as sf

FAILS = []


def check(name, ok, detail=''):
    print(('[통과] ' if ok else '[실패] ') + name + (f'  {detail}' if detail else ''))
    if not ok:
        FAILS.append(name)


def read_csv(path):
    import csv
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        head = next(r)
        rows = np.array([[float(v) for v in row] for row in r], dtype=np.float64)
    assert head[0] == 'time_sec' and len(head) == 256
    return rows[:, 0], rows[:, 1:]


def to_raw(row, W, H, origin=(0.52, 0.41, -0.12), s=0.21):
    """CSV 한 행(255개 피처, 보정본) -> 원시 MediaPipe 좌표. frame_vector 의 역연산."""
    f = H / W
    idx = {n: i for i, n in enumerate(sf.full_names())}
    o = np.array(origin)

    def pts(prefix, n, hand):
        a = np.array([[row[idx[f'{prefix}_{i}_{ax}']] for ax in 'xyz'] for i in range(n)])
        if hand and np.abs(a).sum() == 0:
            return None
        raw = np.empty_like(a)
        raw[:, 0] = o[0] + a[:, 0] * s
        raw[:, 1] = o[1] + (a[:, 1] / f) * s
        raw[:, 2] = (o[2] + a[:, 2] * s) if not hand else a[:, 2] * s
        return raw.tolist()

    return {'pose': pts('pose', 33, False), 'left_hand': pts('lh', 21, True), 'right_hand': pts('rh', 21, True)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv_dir', default=os.path.join('..', 'gesture_dataset_final_ar'))
    ap.add_argument('--n', type=int, default=12, help="검사할 CSV 개수")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.csv_dir, '*_data_*.csv')))[::max(1, 1)]
    if not files:
        raise SystemExit(f"CSV 가 없습니다: {args.csv_dir}")
    step = max(1, len(files) // args.n)
    files = files[::step][:args.n]
    print(f"검사 CSV {len(files)}개\n")

    names = sf.full_names()
    nidx = {n: i for i, n in enumerate(names)}
    cols = sf.model_columns()
    sel = [nidx[c] for c in cols]
    check("model_columns() 183개", len(cols) == 183, str(len(cols)))
    try:
        import train_svm as t
        check("model_columns() == train_svm.feature_sets()['all']", cols == t.feature_sets()['all'])
    except Exception as e:                      # train_svm 을 못 읽는 환경
        t = None
        print(f"(train_svm 을 불러오지 못해 학습 코드와의 비교는 건너뜀: {e})")

    # 1) 프레임 단위 동치성
    max_pose = max_hand = max_ang = 0.0
    n_fr = n_zero_hand = 0
    for p in files:
        times, rows = read_csv(p)
        for r in rows:
            raw = to_raw(r, 1920, 1080)
            v = sf.frame_vector(raw['pose'], raw['left_hand'], raw['right_hand'], 1920, 1080)
            if v is None:
                continue
            ref = r[sel]
            d = np.abs(v - ref)
            max_pose = max(max_pose, d[:27].max())
            max_hand = max(max_hand, d[27:153].max())
            max_ang = max(max_ang, d[153:].max())
            n_fr += 1
            n_zero_hand += int(raw['left_hand'] is None) + int(raw['right_hand'] is None)
    print(f"  프레임 {n_fr}개 비교 (손 없는 경우 {n_zero_hand}회 포함)")
    check("전처리: pose 좌표 최대 오차 < 2e-4", max_pose < 2e-4, f"{max_pose:.2e}")
    check("전처리: 손 좌표 최대 오차 < 2e-4", max_hand < 2e-4, f"{max_hand:.2e}")
    check("전처리: 관절각 최대 오차 < 0.05도", max_ang < 0.05, f"{max_ang:.3f}도")

    # 같은 영상을 다른 해상도 비율로 보냈을 때: 종횡비 보정이 실제로 y 를 바꾸는지 (보정 인자 확인)
    r0 = read_csv(files[0])[1][len(read_csv(files[0])[1]) // 2]
    raw = to_raw(r0, 1920, 1080)
    v169 = sf.frame_vector(raw['pose'], raw['left_hand'], raw['right_hand'], 1920, 1080)
    v43 = sf.frame_vector(raw['pose'], raw['left_hand'], raw['right_hand'], 640, 480)
    iy = [i for i, c in enumerate(cols) if c.startswith('pose_') and c.endswith('_y') and c != 'pose_11_y' and c != 'pose_12_y']
    ratio = np.abs(v43[iy]).sum() / max(np.abs(v169[iy]).sum(), 1e-9)
    check("종횡비 보정: 같은 원시 좌표를 4:3 로 해석하면 y 가 (0.75/0.5625)배", abs(ratio - 0.75 / 0.5625) < 1e-3, f"비율 {ratio:.4f}")

    # 2) 시퀀스 동치성
    max_seq = max_rows = 0.0
    for p in files:
        times, rows = read_csv(p)
        frames = [dict(t=float(tm), **to_raw(r, 1920, 1080)) for tm, r in zip(times, rows)]
        s1, info = sf.sequence_from_frames(frames, 1920, 1080)
        s2, _ = sf.sequence_from_rows(rows, times)
        max_seq = max(max_seq, np.abs(s1 - s2).max())
        if t is not None:
            samples = t.read_csv_full(p)
            for fset in ('all', 'noshoulder'):
                fc = t.feature_sets()[fset]
                Xb, keep = t.build_features(samples, fc, 30)
                ref = Xb[0].reshape(30, len(fc))
                max_rows = max(max_rows, np.abs(sf.select_columns(s2, fc) - ref).max())
    check("시퀀스: frames 입력 == rows 입력", max_seq < 0.06, f"최대 차이 {max_seq:.3g} (관절각 반올림 포함)")
    if t is not None:
        check("시퀀스: rows 입력 == 학습 코드(train_svm.build_features, all·noshoulder 두 집합)", max_rows < 1e-4, f"최대 차이 {max_rows:.2e}")

    # 표준화: 상수 피처(표준편차 ~0)는 입력에 작은 값이 섞여도 0 으로 유지
    mu = np.zeros(183)
    sd = np.ones(183)
    sd[[cols.index('lh_0_z'), cols.index('rh_0_z')]] = 1e-6
    z = np.zeros((30, 183))
    z[:, cols.index('lh_0_z')] = 1e-3
    z[:, 5] = 2.0
    zo = sf.standardize(z, mu, sd)
    check("표준화: 상수 피처는 작은 값이 섞여도 0, 나머지는 그대로", zo[:, cols.index('lh_0_z')].max() == 0 and zo[0, 5] == 2.0)

    # 3) 모델 클래스 동치성, 4) 핸들러
    try:
        import torch
    except Exception as e:
        print(f"(torch 가 없어 3, 4 번은 건너뜀: {e})")
        return finish()
    import inference as inf
    try:
        import train_seq as ts
    except Exception as e:
        ts = None
        print(f"(train_seq 를 불러오지 못해 클래스 비교는 건너뜀: {e})")
    torch.manual_seed(0)
    if ts is not None:
        for kind, (A, B) in (('cnn', (inf.CNN, ts.CNN)), ('gru', (inf.GRU, ts.GRU))):
            a, b = A(183, 7, 64, 0.3).eval(), B(183, 7, 64, 0.3).eval()
            same_keys = list(a.state_dict().keys()) == list(b.state_dict().keys())
            a.load_state_dict(b.state_dict())
            x = torch.randn(3, 30, 183)
            with torch.no_grad():
                dmax = (a(x) - b(x)).abs().max().item()
            check(f"{kind}: inference 와 train_seq 의 state_dict 키 동일, 출력 일치", same_keys and dmax < 1e-6, f"출력 차이 {dmax:.1e}")

    with tempfile.TemporaryDirectory() as d:
        combos = [('cnn', 'all'), ('gru', 'all'), ('cnn', 'noshoulder'), ('gru', 'noshoulder')] if t is not None else [('cnn', 'all'), ('gru', 'all')]
        for kind, fset in combos:
            fcols = t.feature_sets()[fset] if t is not None else cols
            nf = len(fcols)
            kind_label = f"{kind}/{fset}"
            meta = {'arch': kind, 'hidden': 64, 'dropout': 0.3, 'classes': ['가렵다', '가루약', '눈-1372', '간호사'], 'word_ids': [1101, 2108, 1372, 187],
                    'features': fset, 'columns': fcols, 'n_feat': nf, 'frames': 30,
                    'mean': [0.0] * nf, 'std': [1.0] * nf}
            net = inf.build_net(meta)
            md = os.path.join(d, kind_label.replace('/', '_'))
            os.makedirs(md)
            torch.save({k: v.contiguous() for k, v in net.state_dict().items()}, os.path.join(md, 'model.pt'))
            json.dump(meta, open(os.path.join(md, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False)
            model = inf.model_fn(md)
            times, rows = read_csv(files[0])
            payload = {'width': 1920, 'height': 1080, 'top_k': 3,
                       'frames': [dict(t=float(tm), **to_raw(r, 1920, 1080)) for tm, r in zip(times, rows)]}
            body = json.dumps(payload)
            out, ctype = inf.output_fn(inf.predict_fn(inf.input_fn(body.encode('utf-8'), 'application/json'), model))
            res = json.loads(out)
            ok = (res['ok'] and len(res['top_k']) == 3 and 0 < sum(c["prob"] for c in res["top_k"]) <= 1.0001 and
                  res['frames_used'] == len(rows) and ctype == 'application/json')
            check(f"핸들러({kind_label}): 응답 형식", ok, f"top1={res['top_k'][0]['label']} word_id={res['top_k'][0]['word_id']}")
            few = dict(payload, frames=payload['frames'][:3])
            res2 = json.loads(inf.output_fn(inf.predict_fn(few, model))[0])
            check(f"핸들러({kind_label}): 프레임 부족이면 ok=false", res2['ok'] is False and 'reason' in res2)
            nohand = dict(payload, frames=[dict(f, left_hand=None, right_hand=None) for f in payload['frames']])
            res3 = json.loads(inf.output_fn(inf.predict_fn(nohand, model))[0])
            check(f"핸들러({kind_label}): 손이 하나도 없으면 ok=false", res3['ok'] is False)
            nofps = dict(payload, frames=[{k: v for k, v in f.items() if k != 't'} for f in payload['frames']])
            try:
                inf.predict_fn(nofps, model)
                check(f"핸들러({kind_label}): t 도 fps 도 없으면 오류", False)
            except ValueError:
                check(f"핸들러({kind_label}): t 도 fps 도 없으면 오류", True)
            rows_payload = {'rows': rows.tolist(), 'times': times.tolist()}
            res4 = json.loads(inf.output_fn(inf.predict_fn(rows_payload, model))[0])
            check(f"핸들러({kind_label}): rows 입력", res4['ok'])
    finish()


def finish():
    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: {FAILS}")
        sys.exit(1)
    print("모두 통과")


if __name__ == '__main__':
    main()
