"""
최종 학습용 데이터셋 만들기: 기존 234단어 폴더에서 최종 id 만 고르고, 새로 추출한 61개 id 폴더를 합친다. (원본은 건드리지 않는다)
선택 사항으로 가로세로비 보정(y 좌표 x1080/1920, 보정된 손 좌표로 각도 30개 재계산)을 적용한다.

  python build_final.py --mode ar  --out ..\\gesture_dataset_final_ar     # 보정본
  python build_final.py --mode raw --out ..\\gesture_dataset_final        # 보정 없이 합치기만 (비교용)

  기본값: --final_ids ..\\words_final99.txt  --src_old ..\\gesture_dataset_nia234_16spk  --src_new ..\\gesture_dataset_nia61_16spk
  세트: clean(접미사 없음), _mild, _medium, _sa1, _sa2, _severe  (--sets 로 일부만 지정 가능, 예: clean,medium,severe)
  결과 폴더: <out>, <out>_mild, <out>_medium ...   (이미 있는 파일은 건너뛰므로 이어하기 가능, 쓰다 끊긴 파일은 임시 이름으로 쓰고 끝나면 바꾸므로 남지 않는다)

보정 방식: 좌표는 MediaPipe 정규화 좌표(가로·세로를 각각 0~1)를 어깨너비로 나눈 값이라 y 가 영상 가로세로비(1920x1080)만큼 늘어나 있다.
  y 를 1080/1920 배 해서 가로세로가 같은 비율이 되게 하고, 손가락 관절각 30개는 보정한 손 좌표(x,y,z)로 다시 계산한다.
  (원본 저장 좌표로 각도를 다시 계산하면 저장된 각도와 0.004도 차이로 일치해서, 좌표만으로 정확히 재계산된다.)
"""
import argparse
import glob
import os
import re
import shutil
import sys

import numpy as np

SETS = [('clean', ''), ('mild', '_mild'), ('medium', '_medium'), ('sa1', '_sa1'), ('sa2', '_sa2'), ('severe', '_severe')]
V1 = np.array([0, 1, 2, 0, 5, 6, 0, 9, 10, 0, 13, 14, 0, 17, 18])
V2 = np.array([1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19])
V3 = np.array([2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 16, 18, 19, 20])
WORD_RE = re.compile(r'WORD(\d+)', re.I)


def angles(J):
    a = J[V2] - J[V1]
    b = J[V3] - J[V2]
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    na[na == 0] = 1e-6
    nb[nb == 0] = 1e-6
    dot = np.clip((a / na[:, None] * b / nb[:, None]).sum(1), -1.0, 1.0)
    return np.round(np.degrees(np.arccos(dot)), 2)


def fmt(v):
    return str(round(float(v), 5))


def correct_text(txt, yf):
    nl = '\r\n' if '\r\n' in txt else '\n'
    lines = txt.split(nl)
    trailing = lines[-1] == ''
    if trailing:
        lines = lines[:-1]
    hd = lines[0].split(',')
    ix = {h: i for i, h in enumerate(hd)}
    ycols = [i for i, h in enumerate(hd) if h.endswith('_y') and h.startswith(('pose_', 'lh_', 'rh_'))]
    hand_idx = {s: [[ix[f'{s}_{k}_{a}'] for a in 'xyz'] for k in range(21)] for s in ('lh', 'rh')}
    aidx = {'lh': [ix[f'angle_{i}'] for i in range(0, 15)], 'rh': [ix[f'angle_{i}'] for i in range(15, 30)]}
    out = [lines[0]]
    for ln in lines[1:]:
        c = ln.split(',')
        for i in ycols:
            v = float(c[i])
            if v != 0.0:
                c[i] = fmt(v * yf)
        for s in ('lh', 'rh'):
            J = np.array([[float(c[j]) for j in row] for row in hand_idx[s]])
            if np.abs(J).sum() > 0:
                for j, a in zip(aidx[s], angles(J)):
                    c[j] = str(float(a))
        out.append(','.join(c))
    return nl.join(out) + (nl if trailing else '')


def process(src, dst, mode, yf):
    with open(src, 'r', newline='', encoding='utf-8') as f:
        txt = f.read()
    if mode == 'ar':
        txt = correct_text(txt, yf)
    tmp = dst + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        f.write(txt)
    os.replace(tmp, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['ar', 'raw'], required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--final_ids', default=os.path.join('..', 'words_final99.txt'))
    ap.add_argument('--src_old', default=os.path.join('..', 'gesture_dataset_nia234_16spk'))
    ap.add_argument('--src_new', default=os.path.join('..', 'gesture_dataset_nia61_16spk'))
    ap.add_argument('--sets', default='clean,mild,medium,sa1,sa2,severe')
    ap.add_argument('--yfactor', type=float, default=1080 / 1920)
    args = ap.parse_args()

    ids = set(int(x) for x in re.findall(r'\d+', open(args.final_ids, encoding='utf-8-sig').read()))
    want = set(s.strip() for s in args.sets.split(','))
    print(f"최종 id {len(ids)}개, 모드 {args.mode}, 세트 {sorted(want)}")
    for name, suf in SETS:
        if name not in want:
            continue
        dst_dir = args.out + suf
        os.makedirs(dst_dir, exist_ok=True)
        n_old = n_new = n_skip = 0
        found = set()
        for tag, src_base in (('기존', args.src_old), ('신규', args.src_new)):
            d = src_base + suf
            if not os.path.isdir(d):
                print(f"  [{name}] {tag} 폴더 없음: {d}")
                continue
            for p in sorted(glob.glob(os.path.join(d, '*.csv'))):
                m = WORD_RE.search(os.path.basename(p))
                if not m or int(m.group(1)) not in ids:
                    continue
                found.add(int(m.group(1)))
                dst = os.path.join(dst_dir, os.path.basename(p))
                if os.path.exists(dst):
                    n_skip += 1
                    continue
                process(p, dst, args.mode, args.yfactor)
                if tag == '기존':
                    n_old += 1
                else:
                    n_new += 1
        miss = sorted(ids - found)
        have = len(glob.glob(os.path.join(dst_dir, '*.csv')))
        print(f"[{name}] 기존에서 {n_old}개, 신규에서 {n_new}개 새로 만듦, 이미 있어서 건너뜀 {n_skip}개 → 폴더 파일 {have}개"
              + (f" | 파일이 없는 id {len(miss)}개: {miss[:12]}{'...' if len(miss) > 12 else ''}" if miss else ""))
    print("끝")


if __name__ == '__main__':
    main()
