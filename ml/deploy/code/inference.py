"""
SageMaker PyTorch 추론 컨테이너용 핸들러 (model.tar.gz 안의 code/inference.py).

model.tar.gz 구조
  model.pt            state_dict 만 담긴 파일 (torch.load(weights_only=True) 로 읽음)
  meta.json           arch, hidden, dropout, classes, columns, mean, std, frames, n_feat ...
  code/inference.py   이 파일
  code/sign_features.py

요청 (Content-Type: application/json) 은 INPUT_SPEC.md 참고. 요약:
  {"width": 1280, "height": 720, "top_k": 5,
   "frames": [{"t": 0.0, "pose": [[x,y,z] x33], "left_hand": [[x,y,z] x21] 또는 null, "right_hand": ... }, ...]}
  또는 오프라인 검증용: {"rows": [[255개 피처] ...], "times": [...]}
  meta.json 에 "input": "spec2d" 가 있는 모델(명세서 형식)은 (30, 49, 2) 좌표를 받는다:
  {"top_k": 5, "keypoints": [[[x, y] x49] x30]}   (code/spec2d.py 참고. 위의 원시 'frames' 요청도 받아서 같은 형식으로 바꿔 준다)
응답: {"ok": true, "top_k": [{"label": "십-2642", "name": "십", "word_id": 2642, "prob": 0.93}, ...], "frames_in":..,"frames_used":..}
      spec2d 모델은 각 항목에 "label_id" 가 더 들어가고 label 은 단어 이름이다
      프레임이 부족하면 {"ok": false, "reason": "...", ...}  (HTTP 200)
"""
import json
import os
import re

import sys

import numpy as np
import torch
import torch.nn as nn

# 컨테이너가 code/ 를 import 경로에 넣어 주지 않아도 같은 폴더의 sign_features 를 찾도록 한다
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sign_features as sf  # noqa: E402
import spec2d  # noqa: E402

MAX_FRAMES = 900          # 요청 하나에 허용하는 최대 프레임 수 (30fps 기준 30초)
DEFAULT_TOP_K = 5


class CNN(nn.Module):                       # train_seq.CNN 과 같은 구조 (state_dict 키가 같아야 함)
    def __init__(self, n_feat, n_cls, hidden=128, dropout=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_feat, hidden, 5, padding=2), nn.BatchNorm1d(hidden), nn.GELU(),
            nn.Conv1d(hidden, hidden, 5, padding=2), nn.BatchNorm1d(hidden), nn.GELU(), nn.MaxPool1d(2),
            nn.Conv1d(hidden, hidden * 2, 3, padding=1), nn.BatchNorm1d(hidden * 2), nn.GELU(),
        )
        self.drop = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden * 2 * 2, n_cls)

    def forward(self, x):
        h = self.net(x.transpose(1, 2))
        h = torch.cat([h.mean(2), h.amax(2)], dim=1)
        return self.fc(self.drop(h))


class GRU(nn.Module):                       # train_seq.GRU 와 같은 구조
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


def build_net(meta):
    cls = CNN if meta['arch'] == 'cnn' else GRU
    return cls(meta['n_feat'], len(meta['classes']), meta['hidden'], meta['dropout'])


def model_fn(model_dir):
    with open(os.path.join(model_dir, 'meta.json'), encoding='utf-8') as f:
        meta = json.load(f)
    allc = spec2d.column_names() if meta.get('input') == 'spec2d' else sf.model_columns()
    bad = [c for c in meta['columns'] if c not in allc]
    if bad or len(meta['columns']) != meta['n_feat'] or len(meta['mean']) != meta['n_feat'] or len(meta['std']) != meta['n_feat']:
        raise RuntimeError(f"meta.json 의 피처 정의가 sign_features 와 맞지 않습니다 (알 수 없는 열 {bad[:3]}, n_feat {meta['n_feat']})")
    net = build_net(meta)
    state = torch.load(os.path.join(model_dir, 'model.pt'), map_location='cpu', weights_only=True)
    net.load_state_dict(state)
    net.eval()
    return {'net': net, 'meta': meta}


def input_fn(request_body, request_content_type='application/json'):
    if request_content_type != 'application/json':
        raise ValueError(f"application/json 만 지원합니다: {request_content_type}")
    if isinstance(request_body, (bytes, bytearray)):
        request_body = request_body.decode('utf-8')
    data = json.loads(request_body)
    if not isinstance(data, dict):
        raise ValueError("요청은 JSON 객체여야 합니다")
    n = len(data.get('frames') or data.get('rows') or data.get('keypoints') or [])
    if n > MAX_FRAMES:
        raise ValueError(f"프레임이 너무 많습니다: {n} > {MAX_FRAMES}")
    return data


def _split_label(label):
    m = re.match(r'^(.*)-(\d{4})$', label)
    return (m.group(1), int(m.group(2))) if m else (label, None)


def predict_fn(data, model):
    net, meta = model['net'], model['meta']
    top_k = int(data.get('top_k', DEFAULT_TOP_K))
    if meta.get('input') == 'spec2d':
        if 'keypoints' in data:
            kp = np.asarray(data['keypoints'], dtype=np.float64)
            if kp.shape != (spec2d.FRAMES, spec2d.N_POINTS, 2):
                raise ValueError(f"keypoints 는 ({spec2d.FRAMES}, {spec2d.N_POINTS}, 2) 모양이어야 하는데 {kp.shape} 입니다")
            if not np.all(np.isfinite(kp)):
                raise ValueError("keypoints 에 NaN/무한대가 있습니다")
            info = {'frames_in': spec2d.FRAMES, 'frames_used': spec2d.FRAMES}
        elif 'frames' in data:
            for key in ('width', 'height'):
                if key not in data:
                    raise ValueError(f"'{key}'(영상 프레임의 픽셀 크기)가 필요합니다")
            kp, info = spec2d.sample_from_frames(data['frames'], float(data['width']), float(data['height']), data.get('fps'))
        else:
            raise ValueError("'keypoints' (30x49x2) 또는 'frames' 가 필요합니다")
        if kp is None:
            info.setdefault('reason', f"사용 가능한 프레임이 {spec2d.MIN_FRAMES}개 미만입니다 (포즈 또는 손이 검출된 프레임만 셉니다)")
            info['ok'] = False
            return info
        seq = spec2d.select(kp, meta['columns'])
    else:
        if 'frames' in data:
            for key in ('width', 'height'):
                if key not in data:
                    raise ValueError(f"'{key}'(영상 프레임의 픽셀 크기)가 필요합니다")
            seq, info = sf.sequence_from_frames(data['frames'], float(data['width']), float(data['height']), data.get('fps'))
        elif 'rows' in data:
            seq, info = sf.sequence_from_rows(data['rows'], data['times'])
        else:
            raise ValueError("'frames' 또는 'rows' 가 필요합니다")
        if seq is None:
            info.update(ok=False, reason=f"사용 가능한 프레임이 {sf.MIN_FRAMES}개 미만입니다 (포즈 또는 손이 검출된 프레임만 셉니다)")
            return info
        seq = sf.select_columns(seq, meta['columns'])
    x = torch.from_numpy(sf.standardize(seq, meta['mean'], meta['std'])).unsqueeze(0)
    with torch.no_grad():
        prob = torch.softmax(net(x), dim=1)[0].numpy()
    order = np.argsort(-prob)[:max(1, min(top_k, len(prob)))]
    out = []
    for i in order:
        label = meta['classes'][int(i)]
        if meta.get('label_ids'):                     # spec2d 모델: 클래스 이름이 단어 이름이고 label_id 가 labels.csv 의 id
            item = {'label': label, 'name': label, 'label_id': meta['label_ids'][int(i)], 'prob': round(float(prob[i]), 6)}
            if meta.get('word_ids') and meta['word_ids'][int(i)] is not None:
                item['word_id'] = meta['word_ids'][int(i)]
            out.append(item)
            continue
        name, wid = _split_label(label)
        if meta.get('word_ids'):
            wid = meta['word_ids'][int(i)]
        out.append({'label': label, 'name': name, 'word_id': wid, 'prob': round(float(prob[i]), 6)})
    info.update(ok=True, top_k=out)
    return info


def output_fn(prediction, accept='application/json'):
    return json.dumps(prediction, ensure_ascii=False), 'application/json'
