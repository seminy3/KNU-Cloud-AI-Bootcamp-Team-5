"""
시퀀스 타이밍 흔들림 (구간 경계 오차 등) — 평가(robust_seq.py)와 학습 증강(--perturb_train)이 같이 쓴다.

  crop=a  앞뒤를 각각 0~a 비율만큼 잘라냄 (동작 구간 검출이 늦게 시작/일찍 끝남)
  pad=a   앞뒤에 각각 0~a 비율만큼 첫/끝 프레임을 붙잡아 둠 (대기 자세를 포함해 잘림. 실제 대기 동작과는 다르다)
  warp=a  동작 안의 속도를 구간별로 1±a 배로 불균일하게 바꿈
  drop=p  프레임을 확률 p 로 무작위 제거 (시각은 그대로)
"""
import numpy as np

import train_svm as t


def parse_spec(spec):
    out = {}
    for part in spec.split(','):
        k, _, v = part.partition('=')
        k = k.strip()
        if k not in ('crop', 'pad', 'warp', 'drop'):
            raise SystemExit(f"알 수 없는 --perturb 항목: {k} (crop, pad, warp, drop 중에서)")
        out[k] = float(v)
    return out


def perturb_one(arr, sp, rng):
    """(T, 256) 배열 하나의 타이밍을 흔든다. 마지막 열(TIME_COL)은 시각."""
    a = arr.copy()
    tc = t.TIME_COL
    if 'warp' in sp and len(a) >= 5:
        w, k = sp['warp'], 5
        rates = rng.uniform(max(1 - w, 0.05), 1 + w, k)
        x = np.linspace(0, 1, k + 1)
        y = np.concatenate([[0], np.cumsum(rates)]) / rates.sum()
        pos = np.interp(np.linspace(0, 1, len(a)), x, y) * (len(a) - 1)
        src = np.arange(len(a))
        b = a.copy()
        for c in range(tc):
            b[:, c] = np.interp(pos, src, a[:, c])
        a = b
    if 'drop' in sp and len(a) > 6:
        keep = rng.random(len(a)) > sp['drop']
        keep[0] = keep[-1] = True
        if keep.sum() < 5:
            keep[rng.choice(len(a), 5, replace=False)] = True
        a = a[keep]
    if 'crop' in sp:
        n = len(a)
        h, tl = int(rng.uniform(0, sp['crop']) * n), int(rng.uniform(0, sp['crop']) * n)
        if n - h - tl >= 5:
            a = a[h:n - tl]
    if 'pad' in sp:
        n = len(a)
        ph, pt = int(rng.uniform(0, sp['pad']) * n), int(rng.uniform(0, sp['pad']) * n)
        dt = float(np.median(np.diff(a[:, tc]))) if n > 1 else 1 / 30
        dt = dt if np.isfinite(dt) and dt > 0 else 1 / 30
        head = np.repeat(a[:1], ph, axis=0)
        tail = np.repeat(a[-1:], pt, axis=0)
        if ph:
            head[:, tc] = a[0, tc] - dt * np.arange(ph, 0, -1)
        if pt:
            tail[:, tc] = a[-1, tc] + dt * np.arange(1, pt + 1)
        a = np.concatenate([head, a, tail])
    return a



def build_aug(aug_dirs, spec, copies, samples, file_ids, cols, frames, seed):
    """학습 fold 에 추가할 증강 지도를 만든다: --aug_dir 열화본 + (spec 이 있으면) 타이밍을 흔든 복사본 copies 벌.
    흔든 복사본은 파일마다 '깨끗한 원본 또는 열화본 중 하나'를 무작위로 골라 흔든다 (열화와 경계 오차가 같이 오는 경우도 학습).
    samples/file_ids 는 학습에 쓰는 원본(깨끗한) 시퀀스와 파일명이다."""
    aug = t.make_aug(aug_dirs, 0, cols, frames)
    if not spec or copies <= 0:
        return aug
    sp = parse_spec(spec)
    sources = [(samples, list(file_ids))]
    for d in aug_dirs:
        a_samples, _, _, a_fids, _ = t.load_dataset(d)
        sources.append((a_samples, list(a_fids)))
    idx_maps = []
    for _, fids in sources:
        m = {}
        for i, f in enumerate(fids):
            m.setdefault(f, i)
        idx_maps.append(m)
    rng = np.random.default_rng(seed + 13)
    uniq = list(dict.fromkeys(file_ids))
    maps = []
    for c in range(copies):
        pert, names = [], []
        for f in uniq:
            cand = [k for k in range(len(sources)) if f in idx_maps[k]]
            k = cand[int(rng.integers(len(cand)))]
            pert.append(perturb_one(sources[k][0][idx_maps[k][f]], sp, rng))
            names.append(f)
        Xp, keep = t.build_features(pert, cols, frames)
        maps.append({names[i]: Xp[j] for j, i in enumerate(keep)})
        print(f"  perturb_train '{spec}' 복사본 {c + 1}/{copies}: {len(keep)}개", flush=True)
    if aug is None:
        aug = {'copies': 0, 'n_feat': len(cols), 'frames': frames, 'maps': []}
    aug['maps'].extend(maps)
    return aug
