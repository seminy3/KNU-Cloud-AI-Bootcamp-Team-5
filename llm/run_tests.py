"""test_cases.json을 Bedrock에 돌려 문장 품질을 자동 점검한다.

점검 항목 (케이스별)
  - must: 그룹마다 후보 문자열 중 하나는 문장에 있어야 함 (단어 누락 확인)
  - must_not: 문장에 있으면 안 되는 문자열 (지어내기·오역 확인)
  - expect_ambiguous: ambiguous=true 여야 함
  - expect_error: labels.csv에 없는 단어라 요청 전에 거부돼야 함

    python run_tests.py --dry-run                 # Bedrock 호출 없이 요청 내용·단어 검증만
    python run_tests.py                           # 기본 모델(Opus 5.5)
    python run_tests.py --model global.anthropic.claude-haiku-5-5 --only pain_scale,negation_none
결과: results/{모델}_{시각}.csv
"""
import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from sentence import DEFAULT_MODEL, DEFAULT_REGION, build_request, generate, load_labels, make_client

HERE = Path(__file__).parent


def check(case, out):
    s = out["sentence"]
    fails = [f"누락:{'|'.join(g)}" for g in case.get("must", []) if not any(c in s for c in g)]
    fails += [f"금지:{w}" for w in case.get("must_not", []) if w in s]
    if case.get("expect_ambiguous") and not out["ambiguous"]:
        fails.append("ambiguous 아님")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--only", help="쉼표로 구분한 케이스 id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    labels = load_labels()
    cases = json.load(open(HERE / "test_cases.json", encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]

    if args.dry_run:
        bad = 0
        for c in cases:
            try:
                req = build_request(c["words"], labels, c.get("history"), args.model)
                ok = not c.get("expect_error")
                print(f"{'OK ' if ok else 'BAD'} {c['id']}: {req['messages'][0]['content']!r}")
            except ValueError as e:
                ok = c.get("expect_error", False)
                print(f"{'OK ' if ok else 'BAD'} {c['id']}: {e}")
            bad += not ok
        print(f"\nsystem 프롬프트 {len(req['system'][0]['text'])}자, 케이스 {len(cases)}개, 문제 {bad}개")
        return

    client = make_client(args.region)
    rows, passed = [], 0
    for c in cases:
        t0 = time.perf_counter()
        try:
            out = generate(c["words"], labels, c.get("history"), client, args.model)
            fails = ["거부되지 않음"] if c.get("expect_error") else check(c, out)
        except ValueError as e:
            out = {"sentence": "", "ambiguous": None, "usage": {}}
            fails = [] if c.get("expect_error") else [f"오류:{e}"]
        except RuntimeError as e:
            out = {"sentence": "", "ambiguous": None, "usage": {}}
            fails = [f"응답:{e}"]
        ms = int((time.perf_counter() - t0) * 1000)
        passed += not fails
        print(f"{'PASS' if not fails else 'FAIL'} {c['id']:<22} {ms:>5}ms  {out['sentence']}"
              + (f"  [{', '.join(fails)}]" if fails else "") + ("  (ambiguous)" if out["ambiguous"] else ""))
        rows.append([c["id"], " / ".join(c["words"]), out["sentence"], out["ambiguous"], "; ".join(fails), ms,
                     out["usage"].get("input"), out["usage"].get("output"), out["usage"].get("cache_read")])

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{args.model.split('.')[-1]}_{datetime.now():%m%d_%H%M}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "words", "sentence", "ambiguous", "fails", "ms", "input_tokens", "output_tokens", "cache_read"])
        w.writerows(rows)
    lat = sorted(r[5] for r in rows)
    print(f"\n{passed}/{len(cases)} 통과, 지연 중앙값 {lat[len(lat) // 2]}ms → {path}")


if __name__ == "__main__":
    main()
