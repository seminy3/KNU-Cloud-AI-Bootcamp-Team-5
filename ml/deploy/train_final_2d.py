"""
명세서 입력 형식 (30, 49, 2) 으로 178개 단어를 분류하는 모델을 학습해 SageMaker 가 읽는 형식으로 저장한다.

데이터 구성
  AI Hub 86개 클래스: 우리 MediaPipe 추출 CSV (16명 x 99 단어 id, 깨끗한 + 열화 5종) 를 (30, 49, 2) 로 바꿔 쓴다.
                     단어 id -> 클래스는 팀 데이터셋(최종 폴더)의 index.csv 와 같은 규칙 (같은 표제어 id 를 한 클래스로 묶음).
  수어사전 92개 클래스: 팀 데이터셋의 sldict 클립(원본 93 + 증강 829, MediaPipe 추출)을 그대로 쓴다.
  클래스 번호(label_id 0~177)와 이름은 팀 데이터셋의 index.csv 를 따른다.

  python deploy\\train_final_2d.py --holdout REAL02,REAL06           # 평가만: 이 수어자들을 빼고 학습해 정확도를 잰다 (저장 안 함)
  python deploy\\train_final_2d.py --holdout REAL02,REAL06 --boundary_eval   # 평가에 구간 경계 오차(crop/pad) 조건을 추가
  python deploy\\train_final_2d.py --perturb_train 1                        # 학습에 경계 오차 복사본 1벌을 더 넣음
  python deploy\\train_final_2d.py --perturb_train 1 --sldict_repeat 2 --sldict_aug 6   # 수어사전 클립을 반복 대신 수어자 차이 증강 복사본으로
  python deploy\\train_final_2d.py                                   # 최종: 전체 데이터로 학습해 ..\\final_model_cnn_spec2d 에 저장
  python deploy\\train_final_2d.py --model both                      # CNN, GRU 둘 다

  기본 경로: 데이터 ..\\gesture_dataset_final_ar (+ _mild _medium _sa1 _sa2 _severe), 팀 데이터셋 ..\\..\\최종 (index.csv, keypoints\\*.npy)
  변환한 배열은 ..\\spec2d_cache.npz 에 저장해 두고 다시 쓴다 (--no_cache 로 무시).

피처는 기본 spec2d_noface (92개: 코·어깨 제외). 코를 넣으면 카메라 높이가 다를 때 정확도가 크게 떨어진다 (spec2d_compare 결과).
수어사전 클래스는 원본이 1개(수어자 1명)라 평가를 할 수 없고, 학습 때 --sldict_repeat 배로 반복해 클래스 간 샘플 수 차이를 줄인다.
설정은 기존 고정값과 같다 (cnn: hidden 512 / dropout 0.4 / lr 0.01 / wd 0.1 / 300 epoch / smooth 0.4, gru: hidden 384 / dropout 0.5 / lr 0.002 / wd 0.001 / 100 epoch / smooth 0.2).
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, 'code'))

import numpy as np
import torch

import spec2d
import train_seq as ts

CFG = {
    'cnn': dict(hidden=512, dropout=0.4, lr=0.01, wd=0.1, epochs=300, smooth=0.4),
    'gru': dict(hidden=384, dropout=0.5, lr=0.002, wd=0.001, epochs=100, smooth=0.2),
}
AUG_SUFFIX = ['_mild', '_medium', '_sa1', '_sa2', '_severe']
POSE_COLS = [f"pose_{k}_{a}" for k in spec2d.POSE_POINTS for a in 'xy']
HAND_COLS = [f"{s}_{i}_{a}" for s in ('lh', 'rh') for i in range(spec2d.N_HAND) for a in 'xy']
CSV_COLS = POSE_COLS + HAND_COLS                       # 49 점 x 2 축, spec2d.column_names() 와 같은 순서
CLIP_RE = re.compile(r'_data_(REAL\d+)_NIA-SL-WORD(\d+)-', re.I)


def read_team(team_dir):
    """팀 index.csv -> (word_id -> label_id, label_id -> 이름, sldict 행 목록)."""
    with open(os.path.join(team_dir, 'index.csv'), encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    w2c, names, sl = {}, {}, []
    for r in rows:
        lid = int(r['label_id'])
        names.setdefault(lid, r['label'])
        if r['source'] == 'aihub':
            w2c[int(re.search(r'WORD(\d+)', r['clip_id']).group(1))] = lid
        elif r['signer'] == 'sldict':
            sl.append(r)
    return w2c, names, sl


def csv_to_points(path):
    """우리 학습 CSV(이미 어깨 중점·너비로 정규화, y 종횡비 보정됨) -> (30, 49, 2). 쓸 수 없으면 None."""
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        header = next(r)
        rows = [[float(v) for v in x] for x in r]
    if len(rows) < spec2d.MIN_FRAMES:
        return None
    idx = {n: i for i, n in enumerate(header)}
    a = np.array(rows)
    t = a[:, 0]
    pts = a[:, [idx[c] for c in CSV_COLS]].reshape(len(a), spec2d.N_POINTS, 2)
    if not t[-1] > t[0]:
        return None
    return spec2d.resample_points(t, pts).astype(np.float32)


def load_aihub(dirs, w2c, log):
    X, y, spk, fid, tag = [], [], [], [], []
    for d in dirs:
        files = sorted(glob.glob(os.path.join(d, '*.csv')))
        tg = os.path.basename(d.rstrip('\\/'))
        t0 = time.time()
        n0 = len(X)
        for f in files:
            m = CLIP_RE.search(os.path.basename(f))
            if not m or int(m.group(2)) not in w2c:
                continue
            p = csv_to_points(f)
            if p is None:
                continue
            X.append(p)
            y.append(w2c[int(m.group(2))])
            spk.append(m.group(1))
            fid.append(os.path.basename(f))
            tag.append(tg)
        log(f"  {tg}: CSV {len(files)}개 중 {len(X) - n0}개 변환 ({time.time() - t0:.0f}초)")
    return np.stack(X), np.array(y), np.array(spk), np.array(fid), np.array(tag)


def load_sldict(team_dir, sl):
    X, y = [], []
    for r in sl:
        p = os.path.join(team_dir, 'keypoints', r['clip_id'] + '.npy')
        X.append(np.load(p).astype(np.float32))
        y.append(int(r['label_id']))
    return np.stack(X), np.array(y)


def accuracy_report(name, model, mu, sd, cols, X, y, device, names):
    Xn = ((spec2d.select_batch(X, cols) - mu) / sd).astype(np.float32)
    prob = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(Xn), 512):
            prob.append(torch.softmax(model(torch.from_numpy(Xn[i:i + 512]).to(device)), 1).cpu().numpy())
    prob = np.concatenate(prob)
    pred = prob.argmax(1)
    top5 = np.mean([yy in np.argsort(-p)[:5] for yy, p in zip(y, prob)])
    return pred, float((pred == y).mean() * 100), float(top5 * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', choices=['cnn', 'gru', 'both'], default='cnn')
    ap.add_argument('--features', default='spec2d_noface', choices=list(spec2d.feature_sets()))
    ap.add_argument('--data_dir', default=os.path.join('..', 'gesture_dataset_final_ar'))
    ap.add_argument('--aug_suffixes', default=','.join(AUG_SUFFIX), help="열화본 폴더 접미사 (비우면 열화본 없이 학습)")
    ap.add_argument('--team_dir', default=os.path.join('..', '..', '..', '최종'))
    ap.add_argument('--holdout', default='', help="평가용: 빼고 학습할 수어자 (쉼표 구분, 예 REAL02,REAL06). 주면 저장하지 않는다")
    ap.add_argument('--sldict_repeat', type=int, default=8, help="수어사전 클립을 학습 때 반복하는 횟수")
    ap.add_argument('--save', action='store_true', help="--holdout 과 함께 써도 모델을 저장")
    ap.add_argument('--sldict_aug', type=int, default=0, help="수어사전 클립마다 수어자 차이(크기·기울기·수어 공간 위치·속도·잡음)를 준 복사본을 이만큼 더 넣는다. --perturb_train 이 켜져 있으면 경계 오차도 같이 준다. 0 이면 안 넣음")
    ap.add_argument('--perturb_train', type=int, default=0, help="AI Hub 학습 샘플마다 단어 구간 경계 오차(앞뒤 crop 0~0.3 또는 pad 0~0.5)를 준 복사본을 이만큼 더 넣는다. 0 이면 안 넣음")
    ap.add_argument('--boundary_eval', action='store_true', help="--holdout 평가에 구간 경계 오차 조건(crop/pad)을 추가")
    ap.add_argument('--out_dir', default='', help="기본 ..\\final_model_<모델>_spec2d (both 면 접미사 _cnn/_gru)")
    ap.add_argument('--cache', default=os.path.join('..', 'spec2d_cache.npz'))
    ap.add_argument('--no_cache', action='store_true')
    ap.add_argument('--seed', type=int, default=42)
    for k, tp in (('hidden', int), ('dropout', float), ('lr', float), ('wd', float), ('epochs', int), ('smooth', float)):
        ap.add_argument('--' + k, type=tp, default=None, help="기본은 모델별 고정 설정")
    args = ap.parse_args()

    def log(m):
        print(m, flush=True)

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    log(f"device: {device}, torch {torch.__version__}")
    w2c, names, sl = read_team(args.team_dir)
    n_cls = max(names) + 1
    assert sorted(names) == list(range(n_cls)), "label_id 가 0..N-1 이 아닙니다"
    log(f"클래스 {n_cls}개 (AI Hub {len(set(w2c.values()))} + 수어사전, 팀 index.csv), 수어사전 클립 {len(sl)}개")

    base = args.data_dir.rstrip('\\/')
    dirs = [base] + [base + s for s in args.aug_suffixes.split(',') if s.strip()]
    if (not args.no_cache) and os.path.isfile(args.cache):
        z = np.load(args.cache, allow_pickle=False)
        X, y, spk, fid, tag = z['X'], z['y'], z['spk'], z['fid'], z['tag']
        log(f"캐시 사용: {args.cache} ({len(X)}개). 데이터가 바뀌었으면 --no_cache")
    else:
        X, y, spk, fid, tag = load_aihub(dirs, w2c, log)
        if not args.no_cache:
            np.savez_compressed(args.cache, X=X, y=y, spk=spk, fid=fid, tag=tag)
    clean_tag = os.path.basename(base)
    Xs, ys = load_sldict(args.team_dir, sl)
    cols = spec2d.feature_sets()[args.features]
    log(f"AI Hub 변환 샘플 {len(X)} (수어자 {len(set(spk.tolist()))}명), 수어사전 {len(Xs)}, 피처 {args.features} ({len(cols)}개)")

    hold = [h.strip() for h in args.holdout.split(',') if h.strip()]
    tr_mask = ~np.isin(spk, hold)
    parts_x, parts_y = [X[tr_mask]], [y[tr_mask]]
    if args.perturb_train > 0:
        rng = np.random.default_rng(args.seed)
        for _ in range(args.perturb_train):
            parts_x.append(spec2d.random_boundary(X[tr_mask], rng))
            parts_y.append(y[tr_mask])
        log(f"경계 오차 복사본 {args.perturb_train}벌 추가 ({int(tr_mask.sum()) * args.perturb_train}개)")
    n_sa = 0
    if args.sldict_aug > 0:
        rng2 = np.random.default_rng(args.seed + 1)
        for _ in range(args.sldict_aug):
            base_s = spec2d.random_boundary(Xs, rng2) if args.perturb_train > 0 else Xs
            parts_x.append(spec2d.augment_signer(base_s, rng2))
            parts_y.append(ys)
        n_sa = len(Xs) * args.sldict_aug
        log(f"수어사전 증강 복사본 {args.sldict_aug}벌 추가 ({n_sa}개)")
    Xtr = np.concatenate(parts_x + [Xs] * max(1, args.sldict_repeat))
    ytr = np.concatenate(parts_y + [ys] * max(1, args.sldict_repeat))
    log(f"학습 샘플 {len(Xtr)} = AI Hub {int(tr_mask.sum())} (+ 경계 오차 복사본 {int(tr_mask.sum()) * args.perturb_train}) + 수어사전 {len(Xs)} x {max(1, args.sldict_repeat)} (+ 증강 복사본 {n_sa})"
        + (f" (평가용으로 뺀 수어자: {', '.join(hold)})" if hold else ""))
    Xseq = spec2d.select_batch(Xtr, cols)
    mu = Xseq.reshape(-1, len(cols)).mean(0)
    sd = Xseq.reshape(-1, len(cols)).std(0) + 1e-6
    Xn = ((Xseq - mu) / sd).astype(np.float32)

    kinds = ['cnn', 'gru'] if args.model == 'both' else [args.model]
    ok_all = True
    for kind in kinds:
        cfg = dict(CFG[kind])
        for k in ('hidden', 'dropout', 'lr', 'wd', 'epochs', 'smooth'):
            v = getattr(args, k)
            if v is not None:
                cfg[k] = v
        ns = SimpleNamespace(model=kind, bs=128, no_aug=False, **cfg)
        log(f"[{kind}] 학습 시작 {cfg}")
        t0 = time.time()
        model = ts.fit(Xn, ytr.astype(np.int64), n_cls, ns, device, args.seed)
        log(f"[{kind}] 학습 {time.time() - t0:.0f}초")

        if hold:
            log(f"[{kind}] 평가 (빼 둔 수어자 {', '.join(hold)}; 후보는 {n_cls}개 전체)")
            for tg in [clean_tag] + [os.path.basename(d.rstrip('\\/')) for d in dirs[1:]]:
                m = np.isin(spk, hold) & (tag == tg)
                if not m.any():
                    continue
                pred, acc, top5 = accuracy_report(tg, model, mu, sd, cols, X[m], y[m], device, names)
                log(f"    {tg:34s} n={int(m.sum()):4d} 1위 {acc:6.2f}%  상위5 {top5:6.2f}%")
            m = np.isin(spk, hold) & (tag == clean_tag)
            pred, acc, top5 = accuracy_report('clean', model, mu, sd, cols, X[m], y[m], device, names)
            wrong_sl = int(np.sum(~np.isin(pred, list(set(y.tolist()))) ))
            log(f"    깨끗한 영상에서 수어사전 전용 클래스로 잘못 예측: {wrong_sl}개")
            conf = {}
            for yy, pp, ff in zip(y[m], pred, fid[m]):
                if yy != pp:
                    conf[(names[yy], names[pp])] = conf.get((names[yy], names[pp]), 0) + 1
            top = sorted(conf.items(), key=lambda kv: -kv[1])[:8]
            log("    자주 틀린 쌍(정답 -> 예측): " + ', '.join(f"{a}->{b} {n}" for (a, b), n in top))
            if args.boundary_eval:
                m = np.isin(spk, hold) & (tag == clean_tag)
                log(f"[{kind}] 구간 경계 오차 평가 (빼 둔 수어자의 깨끗한 영상 {int(m.sum())}개)")
                conds = [('원본', {}), ('crop 앞뒤 0.1', dict(crop_head=.1, crop_tail=.1)), ('crop 앞뒤 0.2', dict(crop_head=.2, crop_tail=.2)),
                         ('crop 앞뒤 0.3', dict(crop_head=.3, crop_tail=.3)), ('crop 앞뒤 0.4', dict(crop_head=.4, crop_tail=.4)),
                         ('crop 앞만 0.3 (늦게 시작)', dict(crop_head=.3)), ('crop 뒤만 0.3 (일찍 끝)', dict(crop_tail=.3)),
                         ('pad 앞뒤 0.2', dict(pad_head=.2, pad_tail=.2)), ('pad 앞뒤 0.4', dict(pad_head=.4, pad_tail=.4)),
                         ('pad 앞뒤 0.6', dict(pad_head=.6, pad_tail=.6)), ('pad 앞뒤 1.0', dict(pad_head=1., pad_tail=1.)),
                         ('앞 crop 0.2 + 뒤 pad 0.4', dict(crop_head=.2, pad_tail=.4)), ('앞 pad 0.4 + 뒤 crop 0.2', dict(pad_head=.4, crop_tail=.2))]
                for cn, kw in conds:
                    Xe = spec2d.perturb_boundary(X[m], **kw) if kw else X[m]
                    _, acc, top5 = accuracy_report(cn, model, mu, sd, cols, Xe, y[m], device, names)
                    log(f"    {cn:28s} 1위 {acc:6.2f}%  상위5 {top5:6.2f}%")
            if not args.save:
                continue

        out = (args.out_dir + ('_' + kind if args.model == 'both' else '')) if args.out_dir else os.path.join('..', f'final_model_{kind}_spec2d')
        os.makedirs(out, exist_ok=True)
        torch.save({k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}, os.path.join(out, 'model.pt'))
        word_ids = [None] * n_cls
        for wid, lid in sorted(w2c.items()):
            if word_ids[lid] is None:
                word_ids[lid] = wid
        meta = {
            'arch': kind, 'input': 'spec2d', 'hidden': cfg['hidden'], 'dropout': cfg['dropout'],
            'classes': [names[i] for i in range(n_cls)], 'label_ids': list(range(n_cls)), 'word_ids': word_ids,
            'features': args.features, 'columns': cols, 'n_feat': len(cols), 'frames': spec2d.FRAMES,
            'mean': [float(v) for v in mu], 'std': [float(v) for v in sd],
            'train': {'config': cfg, 'seed': args.seed, 'data_dirs': [os.path.abspath(d) for d in dirs],
                      'team_dir': os.path.abspath(args.team_dir), 'holdout': hold, 'sldict_repeat': args.sldict_repeat,
                      'n_samples': int(len(Xtr)), 'n_aihub_used': int(tr_mask.sum()), 'n_sldict': int(len(Xs))},
        }
        with open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        # 저장 검증: 파일에서 다시 읽은 모델이 메모리의 모델과 같은 예측을 내는지
        import inference as inf
        loaded = inf.model_fn(out)
        sub = Xn[:: max(1, len(Xn) // 500)]
        p_mem = ts.predict(model, sub, device)
        p_file = ts.predict(loaded['net'].to(device), sub, device)
        same = float(np.mean(p_mem == p_file)) * 100
        log(f"[{kind}] 저장 검증: 메모리 모델과 파일 모델 예측 일치 {same:.2f}% (학습 데이터 {len(sub)}개)")
        if same < 100.0:
            log(f"[{kind}] 경고: 저장된 모델이 학습한 모델과 다르게 예측합니다. 이 폴더를 배포에 쓰지 마세요: {out}")
            ok_all = False
        else:
            log(f"[{kind}] 저장: {os.path.abspath(out)}")
    sys.stdout.flush()
    os._exit(0 if ok_all else 1)


if __name__ == '__main__':
    main()
