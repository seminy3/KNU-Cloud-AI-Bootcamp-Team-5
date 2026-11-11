"""
시퀀스 모델(1D-CNN / GRU) 단어 분류 — train_svm.py 와 같은 데이터·같은 화자 분리 평가로 비교하기 위한 스크립트.

  python train_seq.py --data_dir ..\\gesture_dataset_nia150 --exclude_speakers SYN --features angles --model cnn
  python train_seq.py ... --model gru --test_dir ..\\gesture_dataset_nia150_medium      # 깨끗한 걸로 학습, 열화본으로 평가
  python train_seq.py ... --with_svm                                                    # 같은 분할에서 SVM 도 같이 평가
  python train_seq.py ... --compare                                                     # 피처 조합 전부 비교

- 입력은 (프레임 30, 피처 수) 시퀀스 그대로 사용 (SVM 처럼 평탄화하지 않음).
- 평가 화자는 학습·조기종료·정규화 통계 어디에도 쓰지 않는다. 에폭 수는 고정(--epochs)이라 평가 데이터로 고르는 일이 없다.
- 학습 중 증강: 가우시안 노이즈, 시간 이동/늘이기(약간), 프레임 드롭아웃. (--no_aug 로 끔)
- 장치는 cuda 가 있으면 자동 사용. 시작할 때 'device:' 줄을 출력한다.
- 배포용 모델은 전체 데이터로 재학습해 gesture_seq_model.pt 로 저장한다 (--no_save 로 생략).
"""
import argparse
import json
import os
import re
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.model_selection import StratifiedGroupKFold

import metrics
import perturb as pt
import train_svm as t


class CNN(nn.Module):
    def __init__(self, n_feat, n_cls, hidden=128, dropout=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_feat, hidden, 5, padding=2), nn.BatchNorm1d(hidden), nn.GELU(),
            nn.Conv1d(hidden, hidden, 5, padding=2), nn.BatchNorm1d(hidden), nn.GELU(), nn.MaxPool1d(2),
            nn.Conv1d(hidden, hidden * 2, 3, padding=1), nn.BatchNorm1d(hidden * 2), nn.GELU(),
        )
        self.drop = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden * 2 * 2, n_cls)      # 평균 + 최대 풀링

    def forward(self, x):                                # x: (B, T, F)
        h = self.net(x.transpose(1, 2))
        h = torch.cat([h.mean(2), h.amax(2)], dim=1)
        return self.fc(self.drop(h))


class GRU(nn.Module):
    def __init__(self, n_feat, n_cls, hidden=128, dropout=0.3):
        super().__init__()
        self.inp = nn.Sequential(nn.Linear(n_feat, hidden), nn.GELU(), nn.Dropout(dropout))
        self.rnn = nn.GRU(hidden, hidden, num_layers=2, batch_first=True, bidirectional=True, dropout=dropout)
        self.drop = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden * 4, n_cls)

    def forward(self, x):
        h, _ = self.rnn(self.inp(x))
        h = torch.cat([h.mean(1), h.amax(1)], dim=1)
        return self.fc(self.drop(h))


def build_net(kind, n_feat, n_cls, args):
    return (CNN if kind == 'cnn' else GRU)(n_feat, n_cls, args.hidden, args.dropout)


def augment(x, strength=1.0):
    """x: (B, T, F) 텐서. 노이즈 + 시간축 이동/신축 + 프레임 드롭아웃."""
    B, T, Fd = x.shape
    x = x + torch.randn_like(x) * 0.05 * strength
    # 시간축 신축/이동: 배치마다 하나의 배율·이동량을 쓰는 대신 샘플별 affine grid
    scale = 1.0 + (torch.rand(B, device=x.device) - 0.5) * 0.3 * strength        # 0.85~1.15
    shift = (torch.rand(B, device=x.device) - 0.5) * 0.2 * strength              # ±0.1
    theta = torch.zeros(B, 2, 3, device=x.device)
    theta[:, 0, 0] = 1.0                                   # 피처축은 그대로
    theta[:, 1, 1] = scale
    theta[:, 1, 2] = shift
    grid = F.affine_grid(theta, (B, 1, T, Fd), align_corners=False)
    x = F.grid_sample(x.unsqueeze(1), grid, mode='bilinear', padding_mode='border', align_corners=False).squeeze(1)
    keep = (torch.rand(B, T, 1, device=x.device) > 0.1 * strength).float()
    return x * keep


def fit(Xtr, ytr, n_cls, args, device, seed):
    """Xtr: (N, T, F) 이미 정규화된 numpy. 고정 에폭 학습 후 모델 반환."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = build_net(args.model, Xtr.shape[2], n_cls, args).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd)
    n = len(Xtr)
    bs = min(args.bs, n)
    steps = args.epochs * int(np.ceil(n / bs))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps)
    X = torch.from_numpy(Xtr).to(device)
    y = torch.from_numpy(ytr.astype(np.int64)).to(device)
    for ep in range(args.epochs):
        model.train()
        perm = torch.randperm(n, device=device)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            if len(b) < 2:
                continue
            xb = X[b]
            if getattr(args, 'shift_fn', None) is not None:      # 카메라 위치 어긋남을 흉내 내는 위치 이동 증강 (robust_seq --shift_aug)
                xb = args.shift_fn(xb)
            if not args.no_aug:
                xb = augment(xb)
            loss = F.cross_entropy(model(xb), y[b], label_smoothing=args.smooth)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    return model


@torch.no_grad()
def predict(model, X, device, bs=1024):
    model.eval()
    out = []
    for i in range(0, len(X), bs):
        out.append(model(torch.from_numpy(X[i:i + bs]).to(device)).argmax(1).cpu())
    return torch.cat(out).numpy()


@torch.no_grad()
def predict_proba(model, X, device, bs=1024):
    """softmax 확률 (N, 클래스 수). top-k·확신도 계산용."""
    model.eval()
    out = []
    for i in range(0, len(X), bs):
        out.append(F.softmax(model(torch.from_numpy(X[i:i + bs]).to(device)), dim=1).cpu())
    return torch.cat(out).numpy().astype(np.float64)


def to_seq(X, n_feat):
    return X.reshape(len(X), -1, n_feat)


def normalize(Xtr, others, eps=1e-6):
    """학습 데이터의 피처별 평균/표준편차로 정규화 (평가 데이터 통계는 쓰지 않음)."""
    mu = Xtr.reshape(-1, Xtr.shape[2]).mean(0)
    sd = Xtr.reshape(-1, Xtr.shape[2]).std(0) + eps
    f = lambda a: ((a - mu) / sd).astype(np.float32)
    return f(Xtr), [f(o) for o in others], (mu, sd)


def cross_validate_seq(X, y, spk, fid, vw, n_cls, n_feat, args, device, Xt=None, with_svm=False, aug=None, seed=None, quiet=False):
    seed = t.SEED if seed is None else seed
    if args.group_by == 'view':
        groups, mode = vw, f"각도 단위 ({len(set(vw))}개, 진단용·낙관적)"
    elif len(set(spk)) >= 2:
        groups, mode = spk, f"화자 단위 ({len(set(spk))}명)"
    else:
        groups, mode = fid, "파일 단위 (화자 정보 없음)"
    n_splits = min(5, len(set(groups)))
    if n_splits < 2:
        return None, None, mode, None, None
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=t.SEED)
    oof = np.full(len(y), -1, dtype=np.int32)
    oof_svm = np.full(len(y), -1, dtype=np.int32) if with_svm else None
    sc = np.full((len(y), n_cls), -np.inf)
    sc_svm = np.full((len(y), n_cls), -np.inf) if with_svm else None
    S = to_seq(X, n_feat)
    St = to_seq(Xt, n_feat) if Xt is not None else S
    for k, (tr, te) in enumerate(cv.split(X, y, groups)):
        t0 = time.time()
        Xf, yf = t.expand_train(X[tr], y[tr], fid[tr], aug, seed + k)     # aug_dir 열화본: 학습 fold 에만 추가
        Xtr, (Xte,), _ = normalize(to_seq(Xf, n_feat), [St[te]])
        model = fit(Xtr, yf, n_cls, args, device, seed + k)
        proba = predict_proba(model, Xte, device)
        sc[te] = proba
        oof[te] = proba.argmax(1)
        msg = f"  fold {k + 1}/{n_splits}: {np.mean(oof[te] == y[te]) * 100:.1f}% ({time.time() - t0:.0f}s)"
        if with_svm:
            Xs = (Xt if Xt is not None else X)[te]
            svm = t.build_model().fit(Xf, yf)
            oof_svm[te] = svm.predict(Xs)
            sc_svm[te] = t.svm_scores(svm, Xs, n_cls)
            msg += f" | SVM {np.mean(oof_svm[te] == y[te]) * 100:.1f}%"
        if not quiet:
            print(msg, flush=True)
    return oof, oof_svm, f"{mode}, {n_splits}-fold", sc, sc_svm


def read_ids(path):
    return {int(x) for x in re.split(r'[,\s]+', open(path, encoding='utf-8').read().strip()) if x}


def focus_report(name, y, pred, fid, n_cls, ids, scores=None, classes=None):
    """ids 에 속한 단어(예: 병원 단어)만 따로 본 정확도, 오답 위치, 오답 목록,
    그리고 scores 가 있으면 후보를 ids 단어로 제한해 다시 고른 정확도(도메인 제한 추론)."""
    cw = {}
    for yy, f in zip(y, fid):
        m = re.search(r'WORD(\d+)', str(f), re.I)
        if m:
            cw[int(yy)] = int(m.group(1))
    is_f = np.array([cw.get(c, -1) in ids for c in range(n_cls)])
    ok = (pred >= 0) & is_f[y]
    if not ok.any():
        print(f"[{name}] 집중 단어 평가 샘플 없음")
        return
    nm = (lambda c: str(classes[c])) if classes is not None else (lambda c: str(c))
    corr = (pred[ok] == y[ok])
    lo, hi = metrics.bootstrap_ci(corr)
    wrong = ok & (pred != y)
    inside = float(is_f[pred[wrong]].mean()) * 100 if wrong.any() else float('nan')
    print(f"[{name}] 집중 단어 {int(is_f.sum())}종, 샘플 {int(ok.sum())}: 정확도 {corr.mean() * 100:.2f}% [{lo * 100:.1f}~{hi * 100:.1f}], "
          f"오답 {int(wrong.sum())}개 중 집중 단어끼리 혼동 {inside:.0f}% / 그 밖의 단어로 {100 - inside if wrong.any() else float('nan'):.0f}%")

    def err_list(yy, pp, title):
        from collections import Counter
        cnt = Counter(zip(yy.tolist(), pp.tolist()))
        print(f"  {title}: 오답 {sum(cnt.values())}개 (정답 → 예측 ×횟수, [밖]=집중 단어 밖)")
        for (a_, b_), n in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {nm(a_)} → {nm(b_)}{'' if is_f[b_] else ' [밖]'} ×{n}")

    err_list(y[wrong], pred[wrong], "제한 없이")
    if scores is not None:
        sub = np.where(is_f)[0]
        pr = sub[np.asarray(scores)[ok][:, sub].argmax(1)]
        corr_r = (pr == y[ok])
        lo2, hi2 = metrics.bootstrap_ci(corr_r)
        print(f"[{name}] 후보를 집중 단어 {len(sub)}종으로 제한: 정확도 {corr_r.mean() * 100:.2f}% [{lo2 * 100:.1f}~{hi2 * 100:.1f}] "
              f"(제한 없을 때 {corr.mean() * 100:.2f}%, 오답 {int((~corr).sum())} → {int((~corr_r).sum())})")
        err_list(y[ok][~corr_r], pr[~corr_r], "제한 후")


def main():
    ap = argparse.ArgumentParser(description="시퀀스 모델(1D-CNN/GRU) 단어 분류 — SVM 과 같은 평가 방식")
    ap.add_argument('--data_dir', default=t.DEFAULT_DATA_DIR)
    ap.add_argument('--out_dir', default=t.HERE)
    ap.add_argument('--frames', type=int, default=t.TARGET_FRAMES)
    ap.add_argument('--features', default='angles', choices=list(t.feature_sets()))
    ap.add_argument('--compare', action='store_true', help="모든 피처 조합을 교차검증으로 비교하고 종료")
    ap.add_argument('--exclude_speakers', default='')
    ap.add_argument('--test_dir', default='', help="평가를 이 폴더의 같은 이름 CSV 로 (학습은 data_dir)")
    ap.add_argument('--group_by', choices=['speaker', 'view'], default='speaker')
    ap.add_argument('--min_speakers', type=int, default=2)
    ap.add_argument('--model', choices=['cnn', 'gru'], default='cnn')
    ap.add_argument('--epochs', type=int, default=60)
    ap.add_argument('--bs', type=int, default=128)
    ap.add_argument('--lr', type=float, default=2e-3)
    ap.add_argument('--wd', type=float, default=1e-2)
    ap.add_argument('--hidden', type=int, default=128)
    ap.add_argument('--dropout', type=float, default=0.3)
    ap.add_argument('--smooth', type=float, default=0.1, help="label smoothing")
    ap.add_argument('--no_aug', action='store_true', help="학습 중 합성 증강(노이즈·시간 신축·프레임 드롭) 끔")
    ap.add_argument('--aug_dir', action='append', default=[],
                    help="학습 영상의 열화본 CSV 폴더 (같은 파일명). 학습 fold 의 해당 파일에만 추가. 여러 번 지정 가능")
    ap.add_argument('--with_svm', action='store_true', help="같은 분할에서 SVM 정확도도 함께 출력")
    ap.add_argument('--no_save', action='store_true')
    ap.add_argument('--perturb_train', default='', help="학습에 구간 경계 오차 등 타이밍 흔들림 복사본 추가 (예: crop=0.3,pad=0.5). perturb.py 참고")
    ap.add_argument('--perturb_copies', type=int, default=2)
    ap.add_argument('--focus_ids_file', default='', help="이 단어 id 들(예: 병원 단어)에 대해서만 정확도·혼동 위치를 따로 출력")
    ap.add_argument('--word_ids_file', default='', help="이 파일(쉼표/공백 구분 단어 id)에 있는 단어만 사용 — 어휘 규모 비교용")
    ap.add_argument('--seed', type=int, default=t.SEED, help="학습 시드 (분할은 고정, 모델 초기화·증강·배치 순서만 바뀜)")
    ap.add_argument('--repeat', type=int, default=1, help="시드를 seed, seed+1, ... 로 N번 반복해 평균±표준편차 출력 (저장·상세 리포트 없음)")
    args = ap.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"device: {device}, torch {torch.__version__}, model {args.model}, epochs {args.epochs}", flush=True)

    samples, labels, speakers, file_ids, views = t.load_dataset(args.data_dir)
    alt = None
    if args.test_dir:
        a_samples, _, _, a_fids, _ = t.load_dataset(args.test_dir)
        alt = (a_samples, a_fids)
        print(f"test_dir: {len(a_samples)}개 샘플 (평가 전용)")
    ex = tuple(s.strip() for s in args.exclude_speakers.split(',') if s.strip())
    if ex:
        keep_i = [i for i, sp in enumerate(speakers) if not str(sp).startswith(ex)]
        print(f"--exclude_speakers {','.join(ex)}: {len(speakers) - len(keep_i)}개 샘플 제외")
        if not keep_i:
            raise SystemExit("제외하고 나니 남은 샘플이 없습니다.")
        samples = [samples[i] for i in keep_i]
        labels, speakers, file_ids, views = labels[keep_i], speakers[keep_i], file_ids[keep_i], views[keep_i]
    if args.word_ids_file:
        ids = read_ids(args.word_ids_file)
        wid = np.array([int(m.group(1)) if (m := re.search(r'WORD(\d+)', str(f), re.I)) else -1 for f in file_ids])
        keep_i = np.where(np.isin(wid, list(ids)))[0]
        print(f"--word_ids_file: 단어 id {len(ids)}개 중 데이터에 있는 샘플 {len(keep_i)}/{len(samples)}개 사용")
        if len(keep_i) == 0:
            raise SystemExit("단어 id 와 일치하는 샘플이 없습니다.")
        samples = [samples[i] for i in keep_i]
        labels, speakers, file_ids, views = labels[keep_i], speakers[keep_i], file_ids[keep_i], views[keep_i]
    sets = t.feature_sets()
    print(f"샘플 {len(samples)}개, 클래스 {len(set(labels))}개, 화자: {', '.join(sorted(set(speakers)))}")

    def run(name, cols, final):
        p = t.prepare(samples, labels, speakers, file_ids, views, cols, args.frames, args.min_speakers)
        if p is None:
            print(f"[{name}] 사용 가능한 샘플 없음")
            return None
        X, y, classes, spk, fid, vw, dropped = p
        Xt = None
        if alt is not None:
            al = t.align_alt(X, y, spk, fid, vw, alt, cols, args.frames)
            if al is None:
                print(f"[{name}] test_dir 에 대응 파일 없음")
                return None
            X, y, spk, fid, vw, Xt, miss = al
        n_feat = len(cols)
        print(f"\n[{name}] 피처 {n_feat}/프레임 × {args.frames}, 샘플 {len(y)}, 클래스 {len(classes)}", flush=True)
        aug = pt.build_aug(args.aug_dir, args.perturb_train, args.perturb_copies, samples, file_ids, cols, args.frames, args.seed)
        if args.repeat > 1:
            accs, svm_acc = [], None
            for r in range(args.repeat):
                sd_ = args.seed + r * 100
                o, o_s, mode, _, _ = cross_validate_seq(X, y, spk, fid, vw, len(classes), n_feat, args, device, Xt=Xt,
                                                        with_svm=(args.with_svm and r == 0), aug=aug, seed=sd_, quiet=True)
                if o is None:
                    print("[경고] 그룹 수가 부족해 교차검증을 건너뜁니다.")
                    return None
                accs.append(t.accuracy(y, o) * 100)
                if o_s is not None:
                    svm_acc = t.accuracy(y, o_s) * 100
                print(f"  seed {sd_}: {args.model.upper()} {accs[-1]:.2f}%", flush=True)
            a = np.array(accs)
            print(f"{args.model.upper()} {len(a)}회 반복 ({mode}): 평균 {a.mean():.2f}% ± {a.std(ddof=1):.2f}%p, 최소 {a.min():.2f}% ~ 최대 {a.max():.2f}%")
            if svm_acc is not None:
                print(f"SVM (결정적, 1회): {svm_acc:.2f}%")
            return None
        oof, oof_svm, mode, sc, sc_svm = cross_validate_seq(X, y, spk, fid, vw, len(classes), n_feat, args, device,
                                                Xt=Xt, with_svm=args.with_svm, aug=aug, seed=args.seed)
        if oof is None:
            print("[경고] 그룹 수가 부족해 교차검증을 건너뜁니다.")
            return None
        grp = vw if args.group_by == 'view' else spk
        tag = ", 평가=test_dir" if alt is not None else ""
        print(f"{args.model.upper()} 정확도: {t.accuracy(y, oof) * 100:.2f}%  ({mode}{tag}, 평가 샘플 {int((oof >= 0).sum())}/{len(y)})")
        print("  평가 그룹별:", t.per_group_acc(y, oof, grp))
        if oof_svm is not None:
            print(f"SVM   정확도: {t.accuracy(y, oof_svm) * 100:.2f}%")
            print("  평가 그룹별:", t.per_group_acc(y, oof_svm, grp))
        m = oof >= 0
        paired = ('SVM', sc_svm[m], oof_svm[m]) if oof_svm is not None else None
        metrics.print_report(y[m], sc[m], classes, grp[m], title=f"{args.model.upper()}, {mode}{tag}", paired=paired, pred=oof[m])
        if args.focus_ids_file:
            fids = read_ids(args.focus_ids_file)
            focus_report(args.model.upper(), y, oof, fid, len(classes), fids, scores=sc, classes=classes)
            if oof_svm is not None:
                focus_report('SVM', y, oof_svm, fid, len(classes), fids, scores=sc_svm, classes=classes)
        if oof_svm is not None:
            metrics.print_report(y[m], sc_svm[m], classes, grp[m], title=f"SVM, {mode}{tag}", pred=oof_svm[m], show_worst=0)
        return X, y, classes, n_feat, oof

    if args.compare:
        for name, cols in sets.items():
            run(name, cols, False)
        return

    res = run(args.features, sets[args.features], True)
    if res is None or args.no_save:
        return
    # 배포용: 전체 데이터로 재학습
    p = t.prepare(samples, labels, speakers, file_ids, views, sets[args.features], args.frames, args.min_speakers)
    X, y, classes = p[0], p[1], p[2]
    n_feat = len(sets[args.features])
    aug = pt.build_aug(args.aug_dir, args.perturb_train, args.perturb_copies, samples, file_ids, sets[args.features], args.frames, args.seed)
    Xe, ye = t.expand_train(X, y, p[4], aug)
    Xn, _, (mu, sd) = normalize(to_seq(Xe, n_feat), [])
    model = fit(Xn, ye, len(classes), args, device, args.seed)
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, 'gesture_seq_model.pt')
    torch.save({'state_dict': {k: v.cpu() for k, v in model.state_dict().items()}, 'arch': args.model,
                'hidden': args.hidden, 'dropout': args.dropout, 'classes': classes, 'frames': args.frames,
                'features': args.features, 'columns': sets[args.features], 'n_feat': n_feat,
                'mean': mu.tolist(), 'std': sd.tolist(), 'versions': t.env_versions(),
                'aug_dirs': args.aug_dir, 'perturb_train': args.perturb_train}, path)
    with open(os.path.join(args.out_dir, 'gesture_labels.json'), 'w', encoding='utf-8') as f:
        json.dump({i: c for i, c in enumerate(classes)}, f, ensure_ascii=False)
    print(f"\n저장: {path}")


if __name__ == '__main__':
    main()
