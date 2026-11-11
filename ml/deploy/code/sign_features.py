"""
수어 단어 분류 모델의 입력 전처리 (numpy 만 사용). 학습 데이터와 같은 계산을 서버/클라이언트가 공통으로 쓰기 위한 모듈이다.

학습 때의 처리 (detector/holistic.py 추출 -> build_final.py --mode ar 보정 -> train_svm.build_features) 를 그대로 옮겼다.

  1) 프레임마다
     - pose 33점, 왼손 21점, 오른손 21점 (MediaPipe Holistic 의 0~1 정규화 이미지 좌표 x, y 와 상대 깊이 z)
     - 원점 = 두 어깨(11, 12)의 중점, 척도 = 어깨 너비 (x, y 정규화 좌표에서의 유클리드 거리)
     - x, y: (값 - 원점) / 척도,  z: pose 는 (z - 원점 z) / 척도, 손은 z / 척도
     - 종횡비 보정: 위 결과의 y 에 (영상 높이 / 영상 너비) 를 곱한다. 학습 영상은 1920x1080 이라 0.5625 였다.
     - 손가락 관절각 30개: 보정된 손 좌표(x, y, z)로 계산 (왼손 15 + 오른손 15), 소수 2자리
     - 검출 안 된 손은 좌표·각도 모두 0.  pose 가 없거나 두 손 모두 없으면 그 프레임은 버린다.
     - 좌표는 소수 5자리로 반올림 (학습 CSV 와 동일)
  2) 전처리는 프레임마다 183개 피처(pose 상체 9점(0, 9~16) x 3 + 양손 126 + 각도 30, train_svm.feature_sets()['all'])를 만들고,
     모델이 쓰는 열만 meta.json 의 'columns' 순서대로 고른다 (예: 'noshoulder' 175개 = 어깨 6개와 손목 z 2개를 뺀 것)
  3) 버리고 남은 프레임을 time_sec 기준으로 선형 보간해 30프레임으로 맞춘다 (train_svm.resample)
  4) 학습 데이터의 피처별 평균/표준편차로 표준화 (model 과 함께 저장된 meta.json)
"""
import numpy as np

AXES = 'xyz'
N_POSE, N_HAND, N_ANGLE = 33, 21, 30
POSE_UPPER = [0, 9, 10, 11, 12, 13, 14, 15, 16]
TARGET_FRAMES = 30
MIN_FRAMES = 5                       # 학습 CSV 읽기 규칙: 5프레임 미만이면 사용하지 않음
TRAIN_ASPECT = 1080 / 1920           # 학습 영상(1920x1080)의 높이/너비. 참고용 상수 (계산에는 입력의 width/height 를 씀)

_V1 = np.array([0, 1, 2, 0, 5, 6, 0, 9, 10, 0, 13, 14, 0, 17, 18])
_V2 = np.array([1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19])
_V3 = np.array([2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 16, 18, 19, 20])


def full_names():
    """CSV 의 255개 피처 열 이름 (time_sec 제외). detector/holistic.py 의 feature_names() 와 같다."""
    names = [f"pose_{i}_{a}" for i in range(N_POSE) for a in AXES]
    names += [f"{s}_{i}_{a}" for s in ('lh', 'rh') for i in range(N_HAND) for a in AXES]
    names += [f"angle_{i}" for i in range(N_ANGLE)]
    return names


def model_columns():
    """모델이 쓰는 183개 피처의 이름과 순서 (train_svm.feature_sets()['all'])."""
    upper = [f"pose_{i}_{a}" for i in POSE_UPPER for a in AXES]
    hands = [f"{s}_{i}_{a}" for s in ('lh', 'rh') for i in range(N_HAND) for a in AXES]
    return upper + hands + [f"angle_{i}" for i in range(N_ANGLE)]


def select_columns(seq, columns):
    """(T, 183) 시퀀스에서 모델이 쓰는 열만 같은 순서로 골라 (T, len(columns)) 로 돌려준다.
    columns 는 model_columns() 의 부분집합이어야 한다 (meta.json 의 'columns')."""
    idx = {n: i for i, n in enumerate(model_columns())}
    missing = [c for c in columns if c not in idx]
    if missing:
        raise ValueError(f"알 수 없는 피처 열: {missing[:3]}")
    return seq[:, [idx[c] for c in columns]]


def hand_angles(joint):
    """joint: (21,3) -> 15개 관절각(도, 소수 2자리). detector/holistic.py hand_angles 와 같다."""
    a = joint[_V2] - joint[_V1]
    b = joint[_V3] - joint[_V2]
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    na[na == 0] = 1e-6
    nb[nb == 0] = 1e-6
    dot = np.clip(np.sum(a / na[:, None] * b / nb[:, None], axis=1), -1.0, 1.0)
    return np.round(np.degrees(np.arccos(dot)), 2)


def _arr(points, n, name):
    if points is None:
        return None
    a = np.asarray(points, dtype=np.float64)
    if a.shape != (n, 3):
        raise ValueError(f"{name}: (n={n}, 3) 모양이어야 하는데 {a.shape} 입니다")
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name}: NaN/inf 가 있습니다")
    return a


def frame_vector(pose, left_hand, right_hand, width, height):
    """한 프레임의 원시 MediaPipe 좌표 -> 모델 피처 183개. 사용할 수 없는 프레임이면 None.
    pose (33,3), left_hand/right_hand (21,3) 또는 None. 좌표는 MediaPipe 가 돌려준 0~1 정규화 값 그대로.
    width, height: 이 좌표를 만든 영상 프레임의 픽셀 크기 (종횡비 보정에 사용)."""
    pose = _arr(pose, N_POSE, 'pose')
    lh = _arr(left_hand, N_HAND, 'left_hand')
    rh = _arr(right_hand, N_HAND, 'right_hand')
    if pose is None or (lh is None and rh is None):
        return None
    if not (width > 0 and height > 0):
        raise ValueError("width, height 는 양수여야 합니다")
    f = float(height) / float(width)

    origin = (pose[11] + pose[12]) / 2.0
    scale = float(np.linalg.norm(pose[11, :2] - pose[12, :2]))
    if scale < 1e-3:
        return None

    pose_n = (pose - origin) / scale
    pose_n[:, 1] *= f
    pose_n = np.round(pose_n, 5)

    hands, angles = [], []
    for h in (lh, rh):
        if h is None:
            hands.append(np.zeros(N_HAND * 3))
            angles.append(np.zeros(15))
            continue
        hn = h.copy()
        hn[:, :2] = (hn[:, :2] - origin[:2]) / scale
        hn[:, 2] = hn[:, 2] / scale
        hn[:, 1] *= f
        hn = np.round(hn, 5)
        hands.append(hn.flatten())
        angles.append(hand_angles(hn))
    vec = np.concatenate([pose_n[POSE_UPPER].flatten()] + hands + angles)
    return vec


def resample(arr, times, target_frames=TARGET_FRAMES):
    """(T, C) -> (target_frames, C). time_sec 를 균등 분할해 선형 보간 (train_svm.resample 과 같은 규칙)."""
    T, C = arr.shape
    src = None
    t = np.asarray(times, dtype=np.float64)
    if len(t) == T and np.all(np.isfinite(t)) and np.all(np.diff(t) >= 0) and t[-1] > t[0]:
        src = (t - t[0]) / (t[-1] - t[0])
    if src is None:
        if T == target_frames:
            return arr
        src = np.linspace(0, 1, T)
    else:
        src = src + np.arange(T) * 1e-12
    dst = np.linspace(0, src[-1], target_frames)
    out = np.empty((target_frames, C), dtype=np.float64)
    for c in range(C):
        out[:, c] = np.interp(dst, src, arr[:, c])
    return out


def sequence_from_frames(frames, width, height, fps=None):
    """frames: [{'t': 초, 'pose': ..., 'left_hand': ..., 'right_hand': ...}, ...] -> (30, 183) 표준화 전 시퀀스.
    t 가 없는 프레임이 하나라도 있으면 fps 로 순번/fps 를 시간으로 쓴다. 반환: (시퀀스, 정보 dict)"""
    use_fps = any(('t' not in fr or fr['t'] is None) for fr in frames)
    if use_fps and not fps:
        raise ValueError("프레임에 't'(초)가 없으면 'fps' 를 주어야 합니다")
    vecs, times = [], []
    for i, fr in enumerate(frames):
        v = frame_vector(fr.get('pose'), fr.get('left_hand'), fr.get('right_hand'), width, height)
        if v is None:
            continue
        vecs.append(v)
        times.append(i / float(fps) if use_fps else float(fr['t']))
    info = {'frames_in': len(frames), 'frames_used': len(vecs)}
    if len(vecs) < MIN_FRAMES:
        return None, info
    bad = [i for i in range(1, len(times)) if not (times[i] >= times[i - 1])]
    if bad:
        raise ValueError(f"프레임 시각 't' 가 단조 증가가 아닙니다 (사용 프레임 {bad[0]}번에서 {times[bad[0] - 1]:.3f} -> {times[bad[0]]:.3f}). 시각이 NaN 이거나 뒤로 갔습니다")
    return resample(np.vstack(vecs), times), info


def sequence_from_rows(rows, times):
    """학습 CSV 와 같은 형식의 행 (각 행 255개 피처, time_sec 제외) -> (30, 183). 검증·오프라인 용도."""
    rows = np.asarray(rows, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != len(full_names()):
        raise ValueError(f"rows 는 (프레임 수, {len(full_names())}) 모양이어야 합니다: {rows.shape}")
    idx = {n: i for i, n in enumerate(full_names())}
    sub = rows[:, [idx[n] for n in model_columns()]]
    info = {'frames_in': len(rows), 'frames_used': len(rows)}
    if len(rows) < MIN_FRAMES:
        return None, info
    return resample(sub, times), info


MIN_STD = 1e-4       # 학습 데이터에서 값이 사실상 상수인 피처(손목 z: 항상 0)의 기준. 정상 피처의 표준편차는 이보다 훨씬 크다 (가장 작은 것이 약 4e-3)


def standardize(seq, mean, std):
    """학습 평균/표준편차로 표준화. 표준편차가 MIN_STD 미만인 상수 피처는 0 으로 둔다.
    (학습 때는 그 피처가 항상 평균과 같아 (x-평균)/1e-6 = 0 이었다. 추론 입력에서 0 이 아닌 값이 조금만 섞여도 수만 배로 커지는 것을 막는다.)"""
    mean = np.asarray(mean, dtype=np.float64)
    std = np.asarray(std, dtype=np.float64)
    const = std < MIN_STD
    out = (seq - mean) / np.where(const, 1.0, std)
    out[..., const] = 0.0
    return out.astype(np.float32)
