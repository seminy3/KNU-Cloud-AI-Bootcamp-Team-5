"""
관절/좌표 시퀀스 CSV → SVM 분류기 학습/평가.

  python train_svm.py --data_dir ..\\gesture_dataset_nia                 # 기본 피처(all), 30프레임
  python train_svm.py --data_dir ..\\gesture_dataset_nia --compare       # 피처 조합별 성능 비교
  python train_svm.py --data_dir ..\\gesture_dataset_nia --features body # 특정 조합으로 학습

CSV 포맷 (data_log.py / extract_nia_zip.py 가 생성)
  full   : time_sec + pose_*(99) + lh_*(63) + rh_*(63) + angle_*(30)   ← 어깨 기준 정규화 좌표 (detector/holistic.py)
  angles : time_sec + angle_0..29 (또는 구형 angle_0..14 단일 손)       ← 구형. 좌표 피처가 필요한 조합에서는 자동 제외
파일명: {label}_data_{speaker}_{id}.csv  (구형: {label}_data_{YYYYMMDD}_{HHMMSS}.csv → 화자 미상)

피처 조합 (--features)
  angles     손 관절각만 (기존 베이스라인)
  hands      양손 21점 좌표 (어깨 기준 위치 + 손 모양)
  pose_upper 코·입꼬리·어깨·팔꿈치·손목 (상체 자세)
  body       pose_upper + hands
  all        body + angles   (기본)

평가: 화자 단위 StratifiedGroupKFold (화자 정보가 없으면 파일 단위 + 경고). 보고 정확도는 교차검증 결과만 사용.
"""
import argparse
import csv
import glob
import json
import os
import re
from collections import Counter

import warnings

import joblib
import numpy as np

import metrics
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

warnings.filterwarnings('ignore', message='The least populated class')
HERE = os.path.dirname(os.path.abspath(__file__))
# ================= Configuration =================
DEFAULT_DATA_DIR = os.path.join(HERE, '..', 'gesture_dataset')
DEFAULT_OUT_DIR = HERE
TARGET_FRAMES = 30      # 각 시퀀스를 이 프레임 수로 리샘플 (아키텍처 문서: 한 단어 = 30프레임)
SVM_C = 10.0
SEED = 42
UNKNOWN_SPEAKER = 'unknown'
POSE_UPPER = [0, 9, 10, 11, 12, 13, 14, 15, 16]   # 코, 입꼬리 2, 어깨 2, 팔꿈치 2, 손목 2
# =================================================

_LEGACY_ID = re.compile(r'^\d{8}_\d{6}$')   # 구형 파일명: {label}_data_20260325_004609


def full_feature_names():
    """detector/holistic.py 의 feature_names() 와 동일 (mediapipe 없이 학습할 수 있게 복제)."""
    names = [f"pose_{i}_{a}" for i in range(33) for a in 'xyz']
    names += [f"{s}_{i}_{a}" for s in ('lh', 'rh') for i in range(21) for a in 'xyz']
    names += [f"angle_{i}" for i in range(30)]
    return names


ALL_NAMES = full_feature_names()
NAME_IDX = {n: i for i, n in enumerate(ALL_NAMES)}
TIME_COL = len(ALL_NAMES)     # read_csv_full 이 돌려주는 배열의 마지막 열 = time_sec (타임스탬프 기준 리샘플용)


def env_versions():
    """모델 파일에 함께 기록할 실행 환경 버전 (나중에 성능이 변했을 때 원인 추적용)."""
    import platform
    import sys
    from importlib import metadata
    out = {'python': platform.python_version(), 'numpy': np.__version__, 'resample': 'timestamp'}
    for pkg in ('scikit-learn', 'mediapipe', 'opencv-python', 'torch'):
        try:
            out[pkg] = metadata.version(pkg)
        except Exception:
            if pkg == 'torch' and 'torch' in sys.modules:
                out[pkg] = sys.modules['torch'].__version__
    return out


def feature_sets():
    angles = [f"angle_{i}" for i in range(30)]
    hands = [n for n in ALL_NAMES if n.startswith(('lh_', 'rh_'))]
    upper = [f"pose_{i}_{a}" for i in POSE_UPPER for a in 'xyz']
    return {
        'angles': angles,
        'hands': hands,
        'pose_upper': upper,
        'body': upper + hands,
        'all': upper + hands + angles,
        # 어깨(11, 12) 좌표는 어깨 중점·너비로 정규화한 뒤라 기울기·좌우 어깨 깊이차만 담는다(12 는 11 의 부호 반전). 손목 z 는 항상 0.
        'noshoulder': [n for n in upper if not n.startswith(('pose_11_', 'pose_12_'))]
                      + [n for n in hands if n not in ('lh_0_z', 'rh_0_z')] + angles,
        # noshoulder 에서 얼굴 위치(코 0, 입 9·10)도 뺀다. 웹캠은 카메라 높이·자세에 따라 이 값이 학습 분포에서 크게 벗어난다.
        'noface': [n for n in upper if not n.startswith(('pose_11_', 'pose_12_', 'pose_0_', 'pose_9_', 'pose_10_'))]
                  + [n for n in hands if n not in ('lh_0_z', 'rh_0_z')] + angles,
        # 기능명세서의 입력 (30, 49, 2) 에 해당하는 2D 피처: 포즈 0, 11~16 과 양손의 x, y 만 (z 와 손가락 각도 없음).
        # 좌표는 우리 학습 데이터 그대로라 y 종횡비 보정이 들어 있다 (명세서에는 이 보정이 없다).
        'spec2d': [f"pose_{i}_{a}" for i in (0, 11, 12, 13, 14, 15, 16) for a in 'xy'] + [n for n in hands if n.endswith(('_x', '_y'))],
        # spec2d 에서 코와 어깨(정규화 뒤 기울기만 남음)를 뺀 것 = 팔꿈치·손목 4점 + 양손
        'spec2d_noface': [f"pose_{i}_{a}" for i in (13, 14, 15, 16) for a in 'xy'] + [n for n in hands if n.endswith(('_x', '_y'))],
        # 참고용 상한: spec2d_noface + 손가락 각도 30개. 각도는 3D 손 좌표에서 계산한 값이라 2D 입력만으로는 같은 값을 못 얻는다.
        'spec2d_noface_angles': [f"pose_{i}_{a}" for i in (13, 14, 15, 16) for a in 'xy'] + [n for n in hands if n.endswith(('_x', '_y'))] + angles,
    }


def parse_name(basename):
    """'quit_data_jimin_20260326_110801.csv' → ('quit', 'jimin'),
    구형 'quit_data_20260326_110801.csv' → ('quit', 'unknown')."""
    stem = os.path.splitext(basename)[0]
    label, rest = stem.split('_data_', 1)
    if _LEGACY_ID.match(rest):
        return label, UNKNOWN_SPEAKER
    return label, rest.split('_')[0]


_VIEW_RE = re.compile(r'_([A-Z])\.csv$')


def parse_view(basename):
    """파일명 끝의 카메라 각도(D/F/L/R/U). 없으면 'unknown'."""
    m = _VIEW_RE.search(basename)
    return m.group(1) if m else 'unknown'


def read_csv_full(path):
    """CSV → (T, 256) 배열: 앞 255열은 피처(파일에 없는 컬럼은 NaN), 마지막 열(TIME_COL)은 time_sec.
    구형 단일 손(15열)은 왼손/오른손 두 변형으로 반환."""
    with open(path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)[1:]                      # time_sec 제외
        raw = [[float(v) for v in r] for r in reader]
    if len(raw) < 5:
        return []
    times = np.array([r[0] for r in raw], dtype=np.float64)
    data = np.array([r[1:] for r in raw], dtype=np.float64)

    if len(header) == 15 and header[0] == 'angle_0':   # 구형 단일 손: 왼손/오른손 자리에 각각 배치
        variants = []
        for offset in (0, 15):
            arr = np.full((len(data), len(ALL_NAMES) + 1), np.nan)
            arr[:, TIME_COL] = times
            base = NAME_IDX['angle_0'] + offset
            arr[:, base:base + 15] = data
            other = NAME_IDX['angle_0'] + (15 - offset)
            arr[:, other:other + 15] = 0.0
            variants.append(arr)
        return variants

    arr = np.full((len(data), len(ALL_NAMES) + 1), np.nan)
    arr[:, TIME_COL] = times
    for j, name in enumerate(header):
        if name in NAME_IDX:
            arr[:, NAME_IDX[name]] = data[:, j]
    return [arr]


def resample(arr, target_frames, times=None):
    """(T, C) → (target_frames, C) 선형 보간.
    times(각 프레임의 time_sec)가 있으면 '시간'을 균등 분할해서 보간한다: 손 검출 실패·fps 변동으로 프레임 간격이
    불규칙해도 동작의 실제 시간 구조가 유지된다 (프레임 번호 기준이면 빠진 구간이 압축됨).
    times 가 없거나 단조 증가가 아니거나 길이가 0이면 프레임 번호 기준으로 되돌아간다."""
    T, C = arr.shape
    src = None
    if times is not None:
        t = np.asarray(times, dtype=np.float64)
        if len(t) == T and np.all(np.isfinite(t)) and np.all(np.diff(t) >= 0) and t[-1] > t[0]:
            src = (t - t[0]) / (t[-1] - t[0])
    if src is None:
        if T == target_frames:
            return arr
        src = np.linspace(0, 1, T)
    else:
        # 같은 시각이 반복되면 np.interp 가 불안정하므로 아주 작게 벌려 단조 증가로 만든다
        src = src + np.arange(T) * 1e-12
    dst = np.linspace(0, src[-1], target_frames)
    out = np.empty((target_frames, C), dtype=np.float64)
    for c in range(C):
        out[:, c] = np.interp(dst, src, arr[:, c])
    return out


def load_dataset(data_dir):
    csv_files = sorted(glob.glob(os.path.join(data_dir, "*_data_*.csv")))
    if not csv_files:
        raise SystemExit(f"CSV 파일이 없습니다: {os.path.abspath(data_dir)}")
    samples, labels, speakers, file_ids, views = [], [], [], [], []
    print(f"{len(csv_files)}개 파일 읽는 중...")
    for fid, path in enumerate(csv_files):
        basename = os.path.basename(path)
        try:
            label, speaker = parse_name(basename)
            variants = read_csv_full(path)
            if not variants:
                print(f"  skip {basename}: 시퀀스가 너무 짧음")
                continue
            for arr in variants:
                samples.append(arr)
                labels.append(label)
                speakers.append(speaker)
                file_ids.append(basename)
                views.append(parse_view(basename))
        except Exception as e:
            print(f"  error {basename}: {e}")
    if not samples:
        raise SystemExit("사용 가능한 샘플이 없습니다.")
    return samples, np.array(labels), np.array(speakers), np.array(file_ids), np.array(views)


def build_features(samples, names, frames):
    """선택한 컬럼이 모두 있는 샘플만 골라 (N, frames*len(names)) 행렬 생성. 사용한 샘플 인덱스도 반환."""
    cols = [NAME_IDX[n] for n in names]
    X, keep = [], []
    for i, arr in enumerate(samples):
        sub = arr[:, cols]
        if np.isnan(sub).any():
            continue
        X.append(resample(sub, frames, arr[:, TIME_COL]).flatten())
        keep.append(i)
    return np.array(X, dtype=np.float32), np.array(keep, dtype=int)


def align_alt(X, y, spk, fid, vw, alt, cols, frames):
    """test_dir 의 같은 이름 파일에서 피처를 만들어 X 와 같은 순서로 정렬. 대응 파일이 없는 샘플은 제외."""
    a_samples, a_fids = alt
    Xa, keep = build_features(a_samples, cols, frames)
    amap = {a_fids[i]: Xa[k] for k, i in enumerate(keep)}
    ok = np.array([f in amap for f in fid])
    if not ok.any():
        return None
    X, y, spk, fid, vw = X[ok], y[ok], spk[ok], fid[ok], vw[ok]
    return X, y, spk, fid, vw, np.array([amap[f] for f in fid], dtype=np.float32), int((~ok).sum())


def augment_X(X, n_feat, frames, copies, rng, time_scale=0.15, time_shift=0.08, jitter=0.05, offset=0.05):
    """학습 데이터 증강 (특징 행렬 수준). 원본 N개로 copies*N개를 새로 만든다 (copies 개 블록을 순서대로 이어 붙임).
      - 시간축 신축/이동: 속도·시작 타이밍 차이 (fps/검출 누락/사람마다 다른 속도)
      - 피처별 가우시안 노이즈: 추출 구현·해상도 차이로 생기는 좌표 떨림 (표준편차의 jitter 배)
      - 샘플 단위 상수 오프셋: 시퀀스 전체에 걸친 체계적 편향 (표준편차의 offset 배)
    평가 데이터에는 쓰지 않는다."""
    N = len(X)
    X3 = X.reshape(N, frames, n_feat).astype(np.float32)
    sd = X3.reshape(-1, n_feat).std(0) + 1e-6
    grid = np.linspace(0, 1, frames)
    out = []
    for _ in range(copies):
        scale = 1.0 + rng.uniform(-time_scale, time_scale, (N, 1))
        shift = rng.uniform(-time_shift, time_shift, (N, 1))
        pos = np.clip(0.5 + (grid[None, :] - 0.5) * scale + shift, 0, 1) * (frames - 1)
        i0 = np.minimum(np.floor(pos).astype(int), frames - 2)
        w = (pos - i0)[..., None].astype(np.float32)
        a = np.take_along_axis(X3, i0[..., None], axis=1)
        b = np.take_along_axis(X3, (i0 + 1)[..., None], axis=1)
        Y = a * (1 - w) + b * w
        Y += rng.normal(0, 1, Y.shape).astype(np.float32) * (jitter * sd)
        Y += rng.normal(0, 1, (N, 1, n_feat)).astype(np.float32) * (offset * sd)
        out.append(Y.reshape(N, -1))
    return np.concatenate(out).astype(np.float32)


def build_aug_maps(aug_dirs, cols, frames):
    """--aug_dir 폴더들(학습 영상의 열화본)에서 {파일명: 피처 벡터} 지도를 만든다."""
    maps = []
    for d in aug_dirs:
        a_samples, _, _, a_fids, _ = load_dataset(d)
        Xa, keep = build_features(a_samples, cols, frames)
        maps.append({a_fids[i]: Xa[k] for k, i in enumerate(keep)})
        print(f"  aug_dir {d}: 사용 가능한 열화본 {len(keep)}개")
    return maps


def make_aug(aug_dirs, copies, cols, frames):
    if not aug_dirs and not copies:
        return None
    return {'copies': copies, 'n_feat': len(cols), 'frames': frames,
            'maps': build_aug_maps(aug_dirs, cols, frames) if aug_dirs else []}


def expand_train(Xtr, ytr, fid_tr, aug, seed=SEED):
    """학습 fold 에만 증강 샘플을 추가: (1) 같은 파일의 열화본(aug_dir), (2) 합성 증강(copies)."""
    if not aug:
        return Xtr, ytr
    Xs, ys = [Xtr], [ytr]
    for m in aug['maps']:
        idx = [i for i, f in enumerate(fid_tr) if f in m]
        if idx:
            Xs.append(np.array([m[fid_tr[i]] for i in idx], dtype=np.float32))
            ys.append(ytr[idx])
    if aug['copies'] > 0:
        rng = np.random.default_rng(seed)
        Xs.append(augment_X(Xtr, aug['n_feat'], aug['frames'], aug['copies'], rng))
        ys.append(np.tile(ytr, aug['copies']))
    return np.concatenate(Xs), np.concatenate(ys)


def build_model():
    return make_pipeline(StandardScaler(), SVC(kernel='rbf', C=SVM_C, gamma='scale'))


def svm_scores(model, X, n_cls, chunk=128):
    """학습에 없던 단어는 -inf 로 채워 (N, n_cls) 점수 행렬을 만든다 (top-k·확신도 계산용).
    단어 수가 많으면 decision_function 의 내부 일대일 행렬(N × n(n-1)/2)이 커서 메모리가 터지므로 chunk 단위로 나눠 계산한다."""
    out = np.full((len(X), n_cls), -np.inf)
    cls = model[-1].classes_
    for i in range(0, len(X), chunk):
        d = model.decision_function(X[i:i + chunk])
        if d.ndim == 1:                      # 클래스 2개일 때
            d = np.stack([-d, d], axis=1)
        out[i:i + chunk][:, cls] = d
    return out


def cross_validate(X, y, speakers, file_ids, verbose=True, views=None, group_by='speaker', X_test=None, aug=None,
                   want_scores=False, n_cls=None):
    """화자 단위(가능하면) 교차검증. (out-of-fold 예측, 설명 문자열) 반환. want_scores=True 면 (예측, 설명, (N, n_cls) 점수 행렬).
    group_by='view' 는 진단용: 카메라 각도 하나를 통째로 빼고 평가 (같은 사람·같은 동작이 학습에 들어가므로 낙관적)."""
    if group_by == 'view':
        groups, mode = views, f"각도 단위 ({len(set(views))}개, 진단용·낙관적)"
    elif len(set(speakers)) >= 2:
        groups, mode = speakers, f"화자 단위 ({len(set(speakers))}명)"
    else:
        groups, mode = file_ids, "파일 단위 (화자 정보 없음)"
        if verbose:
            print("\n[경고] 화자가 1명뿐이라 파일 단위로만 분리합니다. 같은 사람·환경 샘플이 train/test 에 섞여")
            print("       정확도가 실제보다 높게 나옵니다. --speaker 를 지정해 여러 화자를 수집하세요.")
    n_splits = min(5, len(set(groups)))
    if n_splits < 2:
        if verbose:
            print("[경고] 클래스/그룹 수가 부족해 교차검증을 건너뜁니다.")
        return (None, mode, None) if want_scores else (None, mode)
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=SEED)
    oof = np.full(len(y), -1, dtype=np.int32)
    n_cls = n_cls or int(y.max()) + 1
    scores = np.full((len(y), n_cls), -np.inf) if want_scores else None
    for k, (tr, te) in enumerate(cv.split(X, y, groups)):
        Xtr, ytr = expand_train(X[tr], y[tr], file_ids[tr], aug, SEED + k)
        model = build_model().fit(Xtr, ytr)
        Xe = (X_test if X_test is not None else X)[te]
        oof[te] = model.predict(Xe)
        if want_scores:
            scores[te] = svm_scores(model, Xe, n_cls)
    return (oof, f"{mode}, {n_splits}-fold", scores) if want_scores else (oof, f"{mode}, {n_splits}-fold")


def accuracy(y, oof):
    m = oof >= 0
    return float(np.mean(oof[m] == y[m])) if m.any() else float('nan')


def per_group_acc(y, oof, groups):
    parts = []
    for g in sorted(set(groups)):
        m = (groups == g) & (oof >= 0)
        if m.any():
            parts.append(f"{g} {np.mean(oof[m] == y[m]) * 100:.1f}%(n={int(m.sum())})")
    return ", ".join(parts)


def report(y, oof, class_names, mode, scores=None, groups=None):
    m = oof >= 0
    print(f"\n=== 교차검증 결과: {mode} ===")
    print(f"정확도: {accuracy(y, oof) * 100:.2f}%  (평가 샘플 {int(m.sum())}/{len(y)})")
    if len(class_names) <= 30:
        print(classification_report(y[m], oof[m], labels=range(len(class_names)),
                                    target_names=class_names, zero_division=0, digits=3))
    cm = confusion_matrix(y[m], oof[m], labels=range(len(class_names)))
    pairs = sorted(((cm[i, j], class_names[i], class_names[j])
                    for i in range(len(cm)) for j in range(len(cm)) if i != j and cm[i, j] > 0), reverse=True)
    if pairs:
        print("자주 혼동되는 쌍 (정답 → 예측):")
        for n, a, b in pairs[:8]:
            print(f"  {a} → {b}: {n}회")
    if scores is not None:
        metrics.print_report(y[m], scores[m], class_names, groups[m] if groups is not None else None,
                             title=mode, pred=oof[m])


def prepare(samples, labels, speakers, file_ids, views, names, frames, min_speakers=2):
    X, keep = build_features(samples, names, frames)
    if len(keep) == 0:
        return None
    dropped = len(samples) - len(keep)
    # 화자 단위 평가는 '학습에 쓰인 화자와 다른 화자'가 같은 단어를 가져야 의미가 있다.
    # 한 화자에만 있는 단어는 평가 때 맞힐 방법이 없어 정확도만 깎으므로 제외한다.
    if min_speakers > 1 and len(set(speakers[keep])) >= 2:
        spk_of = {}
        for i in keep:
            spk_of.setdefault(labels[i], set()).add(speakers[i])
        ok = np.array([len(spk_of[labels[i]]) >= min_speakers for i in keep])
        keep, X = keep[ok], X[ok]
        if len(keep) == 0:
            return None
    lbl = labels[keep]
    classes = sorted(set(str(c) for c in lbl))
    y = np.array([classes.index(str(c)) for c in lbl], dtype=np.int32)
    return X, y, classes, speakers[keep], file_ids[keep], views[keep], dropped


def main():
    ap = argparse.ArgumentParser(description="관절/좌표 시퀀스 SVM 학습")
    ap.add_argument('--data_dir', default=DEFAULT_DATA_DIR)
    ap.add_argument('--out_dir', default=DEFAULT_OUT_DIR)
    ap.add_argument('--frames', type=int, default=TARGET_FRAMES, help="리샘플 프레임 수")
    ap.add_argument('--features', default='all', choices=list(feature_sets()), help="학습에 쓸 피처 조합")
    ap.add_argument('--compare', action='store_true', help="모든 피처 조합을 교차검증으로 비교하고 종료")
    ap.add_argument('--exclude_speakers', default='',
                    help="이 접두로 시작하는 화자는 제외 (쉼표 구분). 예: SYN → 합성 화자 제외")
    ap.add_argument('--test_dir', default='',
                    help="교차검증 평가를 이 폴더의 '같은 이름' CSV 로 수행 (학습은 data_dir). 예: 열화 영상 폴더")
    ap.add_argument('--group_by', choices=['speaker', 'view'], default='speaker',
                    help="교차검증 분할 기준. view 는 파이프라인 정상 동작 확인용 진단 (실제 성능 아님)")
    ap.add_argument('--min_speakers', type=int, default=2,
                    help="이 수 이상의 화자에서 관측된 단어만 사용 (기본 2, 1이면 필터 없음)")
    ap.add_argument('--aug', type=int, default=0,
                    help="학습 fold 에 합성 증강 샘플을 원본 1개당 이 개수만큼 추가 (시간 신축·노이즈·오프셋). 0 이면 끔")
    ap.add_argument('--aug_dir', action='append', default=[],
                    help="학습 영상의 열화본 CSV 폴더 (같은 파일명). 학습 fold 의 해당 파일에만 추가. 여러 번 지정 가능")
    args = ap.parse_args()

    samples, labels, speakers, file_ids, views = load_dataset(args.data_dir)
    alt = None
    if args.test_dir:
        a_samples, _, _, a_fids, _ = load_dataset(args.test_dir)
        alt = (a_samples, a_fids)
        print(f"test_dir: {len(a_samples)}개 샘플 (평가 전용, 학습에는 data_dir 만 사용)")
    ex = tuple(t.strip() for t in args.exclude_speakers.split(',') if t.strip())
    if ex:
        keep_i = [i for i, sp in enumerate(speakers) if not str(sp).startswith(ex)]
        print(f"--exclude_speakers {','.join(ex)}: {len(speakers) - len(keep_i)}개 샘플 제외")
        if not keep_i:
            raise SystemExit("제외하고 나니 남은 샘플이 없습니다.")
        samples = [samples[i] for i in keep_i]
        labels, speakers, file_ids, views = labels[keep_i], speakers[keep_i], file_ids[keep_i], views[keep_i]
    sets = feature_sets()
    counts = Counter(labels)
    print(f"샘플 {len(samples)}개, 클래스 {len(counts)}개, 화자: {', '.join(sorted(set(speakers)))}")
    print(f"클래스당 샘플 수: 최소 {min(counts.values())} / 최대 {max(counts.values())}")
    spk_per_cls = {}
    for l, sp in zip(labels, speakers):
        spk_per_cls.setdefault(l, set()).add(sp)
    hist = Counter(len(v) for v in spk_per_cls.values())
    print("화자별 샘플 수:", {str(k): v for k, v in sorted(Counter(speakers).items())})
    print("단어가 관측된 화자 수 분포:", {f"{k}명": hist[k] for k in sorted(hist)},
          f"→ --min_speakers {args.min_speakers} 적용 시 사용 가능 단어 {sum(v for k, v in hist.items() if k >= args.min_speakers)}개")

    if args.compare:
        print(f"\n피처 조합 비교 ({args.frames}프레임, 교차검증 정확도)")
        print(f"{'조합':<12}{'피처 수/프레임':>14}{'사용 샘플':>10}{'단어 수':>8}{'정확도':>10}   분할")
        for name, cols in sets.items():
            p = prepare(samples, labels, speakers, file_ids, views, cols, args.frames, args.min_speakers)
            if p is None:
                print(f"{name:<12}{len(cols):>14}{'0':>10}{'0':>8}{'-':>10}   (사용 가능한 샘플 없음)")
                continue
            X, y, classes, spk, fid, vw, dropped = p
            Xt = None
            if alt is not None:
                al = align_alt(X, y, spk, fid, vw, alt, cols, args.frames)
                if al is None:
                    print(f"{name:<12}{len(cols):>14}{'0':>10}{'0':>8}{'-':>10}   (test_dir 에 대응 파일 없음)")
                    continue
                X, y, spk, fid, vw, Xt, miss = al
            aug = make_aug(args.aug_dir, args.aug, cols, args.frames)
            oof, mode, sc = cross_validate(X, y, spk, fid, verbose=False, views=vw, group_by=args.group_by, X_test=Xt, aug=aug,
                                           want_scores=True, n_cls=len(classes))
            if aug:
                mode += f", 증강(aug={args.aug}, aug_dir={len(args.aug_dir)})"
            if alt is not None:
                mode += ", 평가=test_dir"

            acc = f"{accuracy(y, oof) * 100:.2f}%" if oof is not None else "-"
            print(f"{name:<12}{len(cols):>14}{len(y):>10}{len(classes):>8}{acc:>10}   {mode}")
            if oof is not None:
                print(f"{'':<12}  평가 그룹별: {per_group_acc(y, oof, vw if args.group_by == 'view' else spk)}")
                m_ = oof >= 0
                hit = [metrics._topk_hits(y[m_], sc[m_], k).mean() for k in (3, 5)]
                lo, hi = metrics.bootstrap_ci(oof[m_] == y[m_])
                print(f"{'':<12}  95% 신뢰구간 [{lo * 100:.1f}~{hi * 100:.1f}], top-3 {hit[0] * 100:.1f}%, top-5 {hit[1] * 100:.1f}%")
        return

    cols = sets[args.features]
    p = prepare(samples, labels, speakers, file_ids, views, cols, args.frames, args.min_speakers)
    if p is None:
        raise SystemExit(f"'{args.features}' 조합으로 쓸 수 있는 샘플이 없습니다(컬럼 없음 또는 --min_speakers 조건 미충족). (--features angles 를 쓰거나 full 포맷으로 다시 추출)")
    X, y, classes, spk, fid, vw, dropped = p
    if dropped:
        print(f"[알림] '{args.features}' 에 필요한 컬럼이 없는 샘플 {dropped}개 제외")
    print(f"\n피처: {args.features} ({len(cols)}개/프레임 × {args.frames}프레임 = {X.shape[1]}), 샘플 {len(y)}, 클래스 {len(classes)}")
    if len(y) < 10:
        raise SystemExit("데이터가 너무 적습니다. 더 수집하세요.")

    aug = make_aug(args.aug_dir, args.aug, cols, args.frames)
    if aug:
        print(f"증강 학습: 합성 {args.aug}배 + 열화본 폴더 {len(args.aug_dir)}개 (학습 fold 에만 추가, 평가 샘플은 원본/test_dir 그대로)")
    Xt = None
    if alt is not None:
        al = align_alt(X, y, spk, fid, vw, alt, cols, args.frames)
        if al is None:
            raise SystemExit("test_dir 에 data_dir 와 같은 이름의 CSV 가 없습니다.")
        Xc, yc, spkc, fidc, vwc, Xt, miss = al
        if miss:
            print(f"[알림] test_dir 에 대응 파일이 없는 샘플 {miss}개 제외")
        oof, mode, sc = cross_validate(Xc, yc, spkc, fidc, views=vwc, group_by=args.group_by, X_test=Xt, aug=aug,
                                       want_scores=True, n_cls=len(classes))
        mode += ", 평가=test_dir"
        if oof is not None:
            report(yc, oof, classes, mode, scores=sc, groups=vwc if args.group_by == 'view' else spkc)
            print("평가 그룹별 정확도:", per_group_acc(yc, oof, vwc if args.group_by == 'view' else spkc))
        oof = None
    else:
        oof, mode, sc = cross_validate(X, y, spk, fid, views=vw, group_by=args.group_by, aug=aug,
                                       want_scores=True, n_cls=len(classes))
    if oof is not None:
        report(y, oof, classes, mode, scores=sc, groups=vw if args.group_by == 'view' else spk)
        print("평가 그룹별 정확도:", per_group_acc(y, oof, vw if args.group_by == 'view' else spk))

    # 배포용 모델은 전체 데이터로 재학습 (평가는 위 교차검증 수치만 신뢰할 것)
    Xf, yf = expand_train(X, y, fid, aug)
    model = build_model().fit(Xf, yf)
    os.makedirs(args.out_dir, exist_ok=True)
    model_path = os.path.join(args.out_dir, 'gesture_svm_model.joblib')
    label_path = os.path.join(args.out_dir, 'gesture_labels.json')
    joblib.dump({'model': model, 'classes': classes, 'frames': args.frames,
                 'features': args.features, 'columns': cols, 'n_features': int(X.shape[1]),
                 'versions': env_versions(), 'aug': {'copies': args.aug, 'aug_dirs': args.aug_dir}}, model_path)
    with open(label_path, 'w', encoding='utf-8') as f:
        json.dump({i: c for i, c in enumerate(classes)}, f, ensure_ascii=False)
    print(f"\n저장: {model_path}\n저장: {label_path}")


if __name__ == '__main__':
    main()
