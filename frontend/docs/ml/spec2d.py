"""
기능명세서의 입력 형식 (30, 49, 2) 를 다루는 모듈 (numpy 만 사용). 학습 데이터 변환, 추론, 브라우저 구현의 기준 코드로 쓴다.

좌표 형식 (30, 49, 2), float
  프레임 30개, 지점 49개, 축 x y
  지점 0~6   : 코, 왼어깨, 오른어깨, 왼팔꿈치, 오른팔꿈치, 왼손목, 오른손목  (MediaPipe 포즈 0, 11, 12, 13, 14, 15, 16)
  지점 7~27  : 왼손 21점, 지점 28~48: 오른손 21점 (MediaPipe 손 순서, 수어자 본인 기준: 포즈의 왼손목에 붙은 손이 왼손)
  원점 = 두 어깨(포즈 11, 12)의 중점, 척도 = 두 어깨 사이의 거리. 둘 다 픽셀 단위로 잰다 (x 에 영상 너비, y 에 영상 높이를 곱한 좌표).
  검출 안 된 손은 0

원시 MediaPipe 프레임에서 만드는 규칙 (sample_from_frames)
  1) 프레임마다: 포즈가 없거나 두 손이 모두 없거나 어깨 너비(픽셀)가 영상 너비의 0.1% 미만이면 그 프레임은 버린다
  2) 위 정규화로 49점을 만들고, 검출 안 된 손은 0
  3) 남은 프레임을 시각 기준으로 30개 시점에 맞춘다: 앞뒤 프레임을 선형 보간하되, 어느 한쪽에서 그 점(손)이 검출 안 됐으면
     보간하지 않고 가까운 쪽 프레임 값을 그대로 쓴다 (0 과 섞여 절반 크기의 손이 생기는 것을 막는다)
단어 구간을 자르는 일(손을 내린 앞뒤)은 이 모듈이 하지 않는다. 구간 안의 프레임만 넣어야 한다.
"""
import numpy as np

POSE_POINTS = (0, 11, 12, 13, 14, 15, 16)
N_POINTS = 49
N_HAND = 21
FRAMES = 30
MIN_FRAMES = 5
MIN_SHOULDER_RATIO = 0.001


def column_names():
    """(30, 49, 2) 배열을 (30, 98) 로 폈을 때의 열 이름: kp<지점>_<축>, 지점 순서대로 x, y."""
    return [f"kp{p}_{a}" for p in range(N_POINTS) for a in 'xy']


def feature_sets():
    allc = column_names()
    return {
        'spec2d': allc,                                                    # 98개: 명세서의 49점 전부
        'spec2d_noface': [c for c in allc if int(c[2:].split('_')[0]) >= 3],   # 92개: 코·어깨(지점 0~2)를 뺀 것
    }


def select(arr, columns):
    """arr (30, 49, 2) 에서 columns 에 해당하는 열만 골라 (30, len(columns)) 로 돌려준다."""
    arr = np.asarray(arr)
    flat = arr.reshape(arr.shape[0], N_POINTS * 2)
    pos = {n: i for i, n in enumerate(column_names())}
    missing = [c for c in columns if c not in pos]
    if missing:
        raise ValueError(f"알 수 없는 피처 열: {missing[:3]}")
    return flat[:, [pos[c] for c in columns]]


def select_batch(arr, columns):
    """arr (N, 30, 49, 2) -> (N, 30, len(columns)). select 의 배치 버전 (학습용)."""
    arr = np.asarray(arr)
    flat = arr.reshape(arr.shape[0], arr.shape[1], N_POINTS * 2)
    pos = {n: i for i, n in enumerate(column_names())}
    missing = [c for c in columns if c not in pos]
    if missing:
        raise ValueError(f"알 수 없는 피처 열: {missing[:3]}")
    return flat[:, :, [pos[c] for c in columns]]


def _hand(points, width, height, origin, scale):
    if points is None:
        return np.zeros((N_HAND, 2))
    p = np.asarray(points, dtype=np.float64)
    if p.shape[0] != N_HAND or p.ndim != 2 or p.shape[1] < 2:
        raise ValueError(f"손: ({N_HAND}, 2 이상) 모양이어야 하는데 {p.shape} 입니다")
    if not np.all(np.isfinite(p[:, :2])):
        raise ValueError("손 좌표에 NaN/무한대가 있습니다")
    return (p[:, :2] * [width, height] - origin) / scale


def frame_points(pose, left_hand, right_hand, width, height):
    """한 프레임의 원시 MediaPipe 좌표(0~1 정규화) -> (49, 2). 버려야 하는 프레임이면 None."""
    if pose is None or (left_hand is None and right_hand is None):
        return None
    p = np.asarray(pose, dtype=np.float64)
    if p.ndim != 2 or p.shape[0] != 33 or p.shape[1] < 2:
        raise ValueError(f"pose: (33, 2 이상) 모양이어야 하는데 {p.shape} 입니다")
    if not np.all(np.isfinite(p[:, :2])):
        raise ValueError("pose 좌표에 NaN/무한대가 있습니다")
    px = p[:, :2] * [width, height]
    origin = (px[11] + px[12]) / 2
    scale = float(np.linalg.norm(px[11] - px[12]))
    if scale < MIN_SHOULDER_RATIO * width:
        return None
    pose_pts = (px[list(POSE_POINTS)] - origin) / scale
    return np.concatenate([pose_pts, _hand(left_hand, width, height, origin, scale),
                           _hand(right_hand, width, height, origin, scale)], axis=0)


def resample_points(times, pts, target=FRAMES):
    """pts (T, 49, 2), times (T,) 단조 증가 -> (target, 49, 2). 0 은 '검출 안 됨'으로 취급한다 (모듈 설명 3항)."""
    t = np.asarray(times, dtype=np.float64)
    pts = np.asarray(pts, dtype=np.float64)
    T = len(t)
    src = (t - t[0]) / (t[-1] - t[0])
    dst = np.linspace(0.0, 1.0, target)
    i1 = np.clip(np.searchsorted(src, dst, side='left'), 1, T - 1)
    i0 = i1 - 1
    w = np.clip((dst - src[i0]) / np.maximum(src[i1] - src[i0], 1e-12), 0.0, 1.0)
    det = (pts != 0).any(-1)                                   # (T, 49)
    a, b = pts[i0], pts[i1]
    lerp = a * (1 - w)[:, None, None] + b * w[:, None, None]
    nearest = np.where((w > 0.5)[:, None, None], b, a)
    both = (det[i0] & det[i1])[..., None]
    return np.where(both, lerp, nearest)


def sample_from_frames(frames, width, height, fps=None, target=FRAMES):
    """frames: [{'t': 초, 'pose': ..., 'left_hand': ..., 'right_hand': ...}, ...] -> ((target, 49, 2) 또는 None, info)."""
    use_fps = any(('t' not in fr or fr['t'] is None) for fr in frames)
    if use_fps and not fps:
        raise ValueError("프레임에 't'(초)가 없으면 'fps' 를 주어야 합니다")
    pts, times = [], []
    for i, fr in enumerate(frames):
        v = frame_points(fr.get('pose'), fr.get('left_hand'), fr.get('right_hand'), float(width), float(height))
        if v is None:
            continue
        pts.append(v)
        times.append(i / float(fps) if use_fps else float(fr['t']))
    info = {'frames_in': len(frames), 'frames_used': len(pts)}
    if len(pts) < MIN_FRAMES:
        return None, info
    bad = [i for i in range(1, len(times)) if not (times[i] >= times[i - 1])]
    if bad:
        raise ValueError(f"프레임 시각 't' 가 단조 증가가 아닙니다 (사용 프레임 {bad[0]}번에서 {times[bad[0] - 1]:.3f} -> {times[bad[0]]:.3f}). 시각이 NaN 이거나 뒤로 갔습니다")
    if not times[-1] > times[0]:
        info['reason'] = "사용 프레임의 시각이 모두 같습니다"
        return None, info
    return resample_points(times, np.stack(pts)).astype(np.float32), info


def perturb_boundary(X, crop_head=0.0, crop_tail=0.0, pad_head=0.0, pad_tail=0.0):
    """학습·평가용: 단어 구간 경계 오차를 흉내 낸다. X (N, 30, 49, 2) -> (N, 30, 49, 2).
    crop_*: 앞/뒤를 이 비율만큼 잘라냄(동작이 늦게 시작/일찍 끝남으로 잘림), pad_*: 앞/뒤에 이 비율만큼 첫/끝 프레임을 붙잡아 둠(대기 자세 포함).
    실제 대기 동작(손을 올리고 내리는 중)과는 다르다. 자른 뒤 다시 30프레임으로 맞춘다."""
    out = []
    for x in np.asarray(X):
        a = x
        h, t = int(round(crop_head * FRAMES)), int(round(crop_tail * FRAMES))
        if h or t:
            a = a[h:FRAMES - t]
        hh, tt = int(round(pad_head * FRAMES)), int(round(pad_tail * FRAMES))
        if hh or tt:
            a = np.concatenate([np.repeat(a[:1], hh, 0), a, np.repeat(a[-1:], tt, 0)])
        if len(a) < MIN_FRAMES:
            a = x
        out.append(resample_points(np.arange(len(a), dtype=np.float64), a).astype(np.float32))
    return np.stack(out)


def random_boundary(X, rng, max_crop=0.3, max_pad=0.5):
    """각 샘플의 앞·뒤마다 50% 확률로 crop(0~max_crop), 아니면 pad(0~max_pad) 를 무작위로 준 복사본."""
    out = []
    for x in np.asarray(X):
        k = [0.0, 0.0, 0.0, 0.0]                        # crop_head, crop_tail, pad_head, pad_tail
        for side in (0, 1):
            if rng.random() < 0.5:
                k[side] = rng.uniform(0, max_crop)
            else:
                k[2 + side] = rng.uniform(0, max_pad)
        out.append(perturb_boundary(x[None], *k)[0])
    return np.stack(out)


def augment_signer(X, rng, scale=(0.85, 1.15), aniso=0.08, rot_deg=6.0, shift=0.12, noise=0.01, warp=(0.8, 1.25)):
    """학습용: 수어자가 달라서 생기는 차이를 흉내 낸 복사본. X (N, 30, 49, 2) -> (N, 30, 49, 2).
    샘플마다 무작위로 하나씩 뽑아 모든 프레임에 같이 적용한다 (좌표 단위는 어깨 너비).
      크기 scale(전체)·aniso(x, y 따로)·회전 rot_deg(표준편차, 도): 팔 길이, 체형, 카메라 기울기
      shift: 팔꿈치·손목·손(지점 3~48)을 한꺼번에 옮김 (표준편차): 수어 공간의 위치
      noise: 손 지점(7~48)에 프레임마다 더하는 잡음 (표준편차)
      warp: 동작 속도의 변화 (시간축을 u**감마, 감마는 warp 범위의 로그 균등)
    검출 안 된 점(0)은 0으로 둔다. 증강 크기는 임의로 정한 값이고 효과를 재 본 것이 아니다."""
    X = np.asarray(X, dtype=np.float64)
    out = np.empty(X.shape, dtype=np.float32)
    u = np.linspace(0.0, 1.0, FRAMES)
    for i, x in enumerate(X):
        det = (x != 0).any(-1)                                                  # (30, 49)
        s = rng.uniform(*scale)
        sx = s * (1 + rng.uniform(-aniso, aniso))
        sy = s * (1 + rng.uniform(-aniso, aniso))
        th = np.deg2rad(rng.normal(0.0, rot_deg))
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        y = (x * [sx, sy]) @ R.T
        y[:, 3:, :] += rng.normal(0.0, shift, size=2)
        y[:, 7:, :] += rng.normal(0.0, noise, size=(FRAMES, N_POINTS - 7, 2))
        y = np.where(det[..., None], y, 0.0)
        g = float(np.exp(rng.uniform(np.log(warp[0]), np.log(warp[1]))))
        out[i] = resample_points(u ** g, y)
    return out

