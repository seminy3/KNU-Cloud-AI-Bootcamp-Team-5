"""
평가 지표 공통 모듈 (train_svm.py / train_seq.py / eval_model.py 가 같이 쓴다).

print_report(y, scores, classes, groups, ...) 가 한 번에 출력하는 것
  1. 정확도 top-1/3/5  (+ 부트스트랩 95% 신뢰구간)
  2. 그룹(화자)별 정확도의 평균 ± 표준편차, 최소~최대
  3. 계속 틀리는 단어 (단어별 정확도 하위 목록 + 주로 혼동하는 상대)
  4. 확신도 기준 커버리지 대 정확도 ("확신 낮으면 모름 처리" 했을 때 정확도가 어떻게 오르는지)
  5. (선택) 두 모델의 짝지은 비교: 차이의 신뢰구간, 한쪽만 맞힌 샘플 수

scores: (N, 클래스 수) 점수 행렬 — 값이 클수록 그 단어일 가능성이 크다는 뜻이면 된다 (SVM decision_function, 신경망 softmax/logit 모두 가능).
        확률로 보정되어 있지 않아도 되도록, 확신도는 '1등 점수 - 2등 점수'(마진)의 순위로만 쓴다.

주의 (신뢰구간): 샘플 단위 부트스트랩이다. 이 평가 샘플로 잰 정확도가 얼마나 흔들리는지를 보여 줄 뿐,
같은 단어를 여러 화자가 하는 상관은 반영하지 못하므로 실제 불확실성은 이보다 크다. 모델 간 차이가 구간 안에 0 을 포함하면 우열을 말할 수 없다.
"""
from collections import Counter

import numpy as np


def _topk_hits(y, scores, k):
    k = min(k, scores.shape[1])
    top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
    return np.array([y[i] in top[i] for i in range(len(y))])


def bootstrap_ci(values, n_boot=2000, seed=0, alpha=0.05):
    """0/1 배열 평균의 부트스트랩 신뢰구간 → (low, high)."""
    v = np.asarray(values, dtype=np.float64)
    if len(v) == 0:
        return float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), (n_boot, len(v)))
    m = v[idx].mean(axis=1)
    return float(np.quantile(m, alpha / 2)), float(np.quantile(m, 1 - alpha / 2))


def paired_diff(correct_a, correct_b, n_boot=2000, seed=0):
    """같은 샘플에 대한 두 모델의 정오 배열 → (평균 차이 a-b, 하한, 상한, a만 맞힘, b만 맞힘)."""
    a = np.asarray(correct_a, dtype=np.float64)
    b = np.asarray(correct_b, dtype=np.float64)
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), (n_boot, len(d)))
    m = d[idx].mean(axis=1)
    return (float(d.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975)),
            int(((a == 1) & (b == 0)).sum()), int(((a == 0) & (b == 1)).sum()))


def margin(scores):
    """1등 점수 - 2등 점수."""
    if scores.shape[1] < 2:
        return np.zeros(len(scores))
    part = -np.partition(-scores, 1, axis=1)[:, :2]
    return part.max(axis=1) - part.min(axis=1)


def coverage_table(correct, conf, levels=(1.0, 0.9, 0.8, 0.7, 0.6, 0.5)):
    """확신도 높은 순으로 levels 비율만 처리했을 때의 (커버리지, 처리 수, 정확도, 기각분 중 오답 비율) 행 목록."""
    order = np.argsort(-conf, kind='stable')
    c = np.asarray(correct)[order]
    n = len(c)
    rows = []
    for lv in levels:
        k = max(1, int(round(lv * n)))
        acc = float(c[:k].mean())
        rej = c[k:]
        rows.append((lv, k, acc, (float(1 - rej.mean()) if len(rej) else float('nan'))))
    return rows


def worst_words(y, pred, classes, top=10):
    """단어별 정확도 오름차순 (정확도 < 100% 인 단어만) → 행: (이름, 정확도, 샘플 수, 가장 흔한 오답)."""
    rows = []
    for c in sorted(set(y.tolist())):
        m = y == c
        acc = float(np.mean(pred[m] == c))
        if acc < 1.0:
            wrong = Counter(pred[m][pred[m] != c].tolist()).most_common(1)
            rows.append((classes[c], acc, int(m.sum()), classes[wrong[0][0]] if wrong else '-'))
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows[:top], len(rows)


def group_stats(y, pred, groups):
    vals = {}
    for g in sorted(set(groups.tolist())):
        m = groups == g
        if m.any():
            vals[g] = (float(np.mean(pred[m] == y[m])), int(m.sum()))
    return vals


def print_report(y, scores, classes, groups=None, title='', ks=(1, 3, 5), paired=None, show_worst=10, pred=None):
    """y: 정답 인덱스 (N,), scores: (N, C). groups: 화자 등 그룹 라벨 (N,). paired: (이름, 비교 모델 scores, 비교 모델 예측 또는 None).
    pred: 모델이 실제로 낸 예측 (없으면 scores 의 argmax). SVM 은 predict 와 decision_function 의 argmax 가 드물게 다를 수 있어 따로 받는다."""
    y = np.asarray(y)
    scores = np.asarray(scores, dtype=np.float64)
    ok = np.isfinite(scores).any(axis=1)          # 평가되지 않은 샘플(점수 없음) 제외
    pred_in = np.asarray(pred) if pred is not None else None
    y, scores = y[ok], scores[ok]
    pred_in = pred_in[ok] if pred_in is not None else None
    groups = np.asarray(groups)[ok] if groups is not None else None
    n = len(y)
    if n == 0:
        print("(평가 샘플 없음)")
        return
    pred = pred_in if pred_in is not None else scores.argmax(1)
    correct = pred == y
    print(f"\n--- 평가 지표{(' — ' + title) if title else ''} (샘플 {n}, 단어 {len(set(y.tolist()))}종) ---")

    # 1. top-k + 신뢰구간
    parts = []
    for k in ks:
        hit = correct if k == 1 else _topk_hits(y, scores, k)
        lo, hi = bootstrap_ci(hit)
        parts.append(f"top-{k} {hit.mean() * 100:.2f}% [{lo * 100:.1f}~{hi * 100:.1f}]")
    print("정확도 (95% 신뢰구간):  " + "   ".join(parts))
    print(f"  (샘플 1개 = {100 / n:.2f}%p. 신뢰구간은 샘플 단위 부트스트랩이라 실제 불확실성보다 좁게 나온다)")

    # 2. 그룹별 평균 ± 표준편차
    if groups is not None:
        gs = group_stats(y, pred, groups)
        if len(gs) >= 2:
            a = np.array([v[0] for v in gs.values()])
            lo_g, hi_g = min(gs, key=lambda g: gs[g][0]), max(gs, key=lambda g: gs[g][0])
            print(f"그룹별 정확도: 평균 {a.mean() * 100:.2f}% ± {a.std(ddof=1) * 100:.2f}%p ({len(a)}개 그룹), "
                  f"최저 {lo_g} {gs[lo_g][0] * 100:.1f}% ~ 최고 {hi_g} {gs[hi_g][0] * 100:.1f}%")

    # 3. 계속 틀리는 단어
    rows, n_bad = worst_words(y, pred, classes, show_worst)
    n_cls = len(set(y.tolist()))
    zero = sum(1 for c in set(y.tolist()) if not np.any(pred[y == c] == c))
    print(f"단어별: 전부 맞힌 단어 {n_cls - n_bad}/{n_cls}, 하나도 못 맞힌 단어 {zero}")
    if rows:
        print(f"  정확도 낮은 단어 (정답 → 주로 혼동한 단어):")
        for name, acc, cnt, wrong in rows:
            print(f"    {name}: {acc * 100:.0f}% (n={cnt}) → {wrong}")

    # 4. 커버리지 대 정확도
    conf = margin(scores)
    print("확신도(1등-2등 점수 차) 순으로 일부만 처리했을 때:")
    print(f"  {'처리 비율':>8}{'처리 수':>8}{'정확도':>9}{'기각분 중 오답':>16}")
    for lv, k, acc, rej_wrong in coverage_table(correct, conf):
        rw = f"{rej_wrong * 100:.0f}%" if rej_wrong == rej_wrong else '-'
        print(f"  {lv * 100:>7.0f}%{k:>8}{acc * 100:>8.1f}%{rw:>16}")

    # 5. 짝지은 모델 비교
    if paired is not None:
        name, other = paired[0], np.asarray(paired[1], dtype=np.float64)[ok]
        other_pred = np.asarray(paired[2])[ok] if len(paired) > 2 and paired[2] is not None else other.argmax(1)
        other_correct = other_pred == y
        d, lo, hi, a_only, b_only = paired_diff(correct, other_correct)
        verdict = "차이 있음" if (lo > 0 or hi < 0) else "구간에 0 포함 → 우열 판단 불가"
        print(f"짝지은 비교 (이 모델 - {name}): {d * 100:+.2f}%p [{lo * 100:+.1f}~{hi * 100:+.1f}], "
              f"이 모델만 맞힘 {a_only} / {name}만 맞힘 {b_only}  → {verdict}")
