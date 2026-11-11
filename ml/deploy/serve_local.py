"""
AWS 없이 엔드포인트와 같은 계약(GET /ping, POST /invocations)으로 추론 코드를 로컬에서 띄우고 호출해 본다.
SageMaker 컨테이너가 하는 일(model_fn -> input_fn -> predict_fn -> output_fn)을 그대로 호출하므로, 같은 요청 JSON 이 배포 후에도 같은 결과를 낸다.
(컨테이너 환경 자체, 즉 이미지·권한·네트워크는 검증하지 않는다.)

  # 터미널 1: 서버
  python deploy\\serve_local.py --model_dir ..\\final_model_cnn --port 8080
  # 터미널 2: CSV 한 개를 원시 좌표 요청으로 바꿔 호출 (여러 번 호출해 시간도 잼)
  python deploy\\serve_local.py --client --csv ..\\gesture_dataset_final_ar\\<파일>.csv --port 8080 --repeat 5
  # 직접 만든 요청 JSON 으로 호출
  python deploy\\serve_local.py --client --payload request.json --port 8080
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))
sys.path.insert(0, HERE)


def serve(model_dir, port):
    import inference as inf
    model = inf.model_fn(model_dir)
    print(f"모델 로드: {model['meta']['arch']}, 클래스 {len(model['meta']['classes'])}개. http://127.0.0.1:{port}  (GET /ping, POST /invocations)", flush=True)

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype='application/json'):
            b = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == '/ping':
                self._send(200, '{}')
            else:
                self._send(404, '{"error":"not found"}')

        def do_POST(self):
            if self.path != '/invocations':
                return self._send(404, '{"error":"not found"}')
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            t0 = time.time()
            try:
                data = inf.input_fn(body, self.headers.get('Content-Type', 'application/json').split(';')[0])
                out, ctype = inf.output_fn(inf.predict_fn(data, model))
                self._send(200, out, ctype)
                print(f"POST /invocations {len(body) / 1e3:.1f} KB -> 200 ({(time.time() - t0) * 1000:.0f} ms)", flush=True)
            except Exception as e:
                self._send(400, json.dumps({'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False))
                print(f"POST /invocations -> 400 {type(e).__name__}: {e}", flush=True)

        def log_message(self, *a):
            pass

    HTTPServer(('127.0.0.1', port), H).serve_forever()


def client(a):
    if a.csv:
        import test_local as tl
        times, rows = tl.read_csv(a.csv)
        payload = {'width': 1920, 'height': 1080, 'top_k': a.top_k,
                   'frames': [dict(t=float(tm), **tl.to_raw(r, 1920, 1080)) for tm, r in zip(times, rows)]}
    else:
        payload = json.load(open(a.payload, encoding='utf-8'))
    body = json.dumps(payload).encode('utf-8')
    print(f"요청 {len(body) / 1e3:.1f} KB, 프레임 {len(payload.get('frames', []))}개")
    for i in range(a.repeat):
        req = urllib.request.Request(f'http://127.0.0.1:{a.port}/invocations', data=body, headers={'Content-Type': 'application/json'})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req) as r:
                out = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise SystemExit(f"HTTP {e.code}: {e.read().decode('utf-8')}")
        if i == 0:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"  호출 {i + 1}/{a.repeat}: {(time.time() - t0) * 1000:.0f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model_dir', default='')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--client', action='store_true')
    ap.add_argument('--csv', default='')
    ap.add_argument('--payload', default='')
    ap.add_argument('--top_k', type=int, default=5)
    ap.add_argument('--repeat', type=int, default=3)
    a = ap.parse_args()
    if a.client:
        if not (a.csv or a.payload):
            raise SystemExit("--csv 또는 --payload 가 필요합니다")
        client(a)
    else:
        if not a.model_dir:
            raise SystemExit("--model_dir 가 필요합니다")
        serve(a.model_dir, a.port)


if __name__ == '__main__':
    main()
