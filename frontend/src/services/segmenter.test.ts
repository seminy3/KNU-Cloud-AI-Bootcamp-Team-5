import {describe, it, expect} from 'vitest';
import {Segmenter, handsUp} from './segmenter';
import type {Point} from '../types';

// 왼손 손목(7번)의 높이만 바꾼 49점 프레임. y가 작을수록 위(어깨 쪽)다.
const frame = (wristY: number): Point[] => Array.from({length: 49}, (_, i): Point => i === 7 ? [0.2, wristY] : i >= 28 ? [0, 0] : [0.1, 0.3]);
const UP = 0.4, DOWN = 2.0, FPS = 30;

function run(seg: Segmenter, plan: [number | null, number][], start = 0) {
  // plan: [손목 높이(null이면 손 없음), 초]
  const events: NonNullable<ReturnType<Segmenter['push']>>[] = [];
  let t = start;
  for (const [y, seconds] of plan) {
    for (let i = 0; i < Math.round(seconds * FPS); i++) {
      const ev = seg.push(t, y === null ? null : frame(y));
      if (ev) events.push(ev);
      t += 1 / FPS;
    }
  }
  return {events, t};
}

describe('segmenter', () => {
  it('detects raised hands only when a hand wrist is above the threshold', () => {
    expect(handsUp(frame(UP))).toBe(true);
    expect(handsUp(frame(DOWN))).toBe(false);
    expect(handsUp(null)).toBe(false);
  });

  it('records from hand raise (with pre-roll) until hands are lowered', () => {
    const {events} = run(new Segmenter(), [[DOWN, 1], [UP, 1.5], [DOWN, 1]]);
    expect(events.map(e => e.type)).toEqual(['start', 'end']);
    const end = events[1] as {type: 'end'; frames: {time: number}[]};
    const span = end.frames.at(-1)!.time - end.frames[0].time;
    expect(span).toBeGreaterThan(1.5);   // 시작 직전 여유 포함
    expect(span).toBeLessThan(2.0);      // 내린 뒤 0.5초 대기 구간은 대부분 뺀다
  });

  it('treats frames without hands as lowered', () => {
    const {events} = run(new Segmenter(), [[UP, 1], [null, 1]]);
    expect(events.map(e => e.type)).toEqual(['start', 'end']);
  });

  it('discards very short motions', () => {
    const {events} = run(new Segmenter(), [[DOWN, 0.5], [UP, 0.15], [DOWN, 1]]);
    expect(events.map(e => e.type)).toEqual(['start', 'discard']);
  });

  it('needs hands lowered before the next word', () => {
    const seg = new Segmenter();
    let r = run(seg, [[UP, 1]]);
    expect(seg.finish()?.type).toBe('end');          // 버튼으로 끝냄
    r = run(seg, [[UP, 1]], r.t);                     // 계속 들고 있으면 다시 시작하지 않는다
    expect(r.events).toEqual([]);
    r = run(seg, [[DOWN, 0.3], [UP, 1], [DOWN, 1]], r.t);
    expect(r.events.map(e => e.type)).toEqual(['start', 'end']);
  });

  it('gives up after 15 seconds and ignores input while held', () => {
    const seg = new Segmenter();
    expect(run(seg, [[UP, 16]]).events.map(e => e.type)).toEqual(['start', 'discard']);
    seg.reset();
    seg.hold();
    expect(run(seg, [[UP, 1]]).events).toEqual([]);
  });
});

import {normalize, assignHands, type Landmark} from './keypointNormalizer';
import real from '../../tests/fixtures/golden_real.json';

describe('segmenter on the real webcam fixture', () => {
  it('keeps the signing part of a real recording', () => {
    const lm = (p: number[][] | null): Landmark[] => (p || []).map(q => ({x: q[0], y: q[1]}));
    const {width, height, frames} = real.input;
    const seg = new Segmenter();
    const events = [];
    // 이 녹화는 처음 약 0.8초 동안 손을 무릎에 둔 채(손목 높이 1.35~1.5) 검출되고, 수어할 때 0.3~1.0까지 올라간다
    let firstRaise = Infinity, last = 0;
    const raised: number[] = [];
    for (const f of frames) {
      const pose = lm(f.pose);
      const points = normalize({pose, ...assignHands(pose, [lm(f.left_hand), lm(f.right_hand)].filter(h => h.length), width, height)}, width, height);
      if (points && handsUp(points)) firstRaise = Math.min(firstRaise, f.t);
      if (points && f.t >= firstRaise) raised.push(f.t);
      const ev = seg.push(f.t, points);
      if (ev) events.push(ev);
      last = f.t;
    }
    // 녹화가 동작 중에 끝나므로, 손을 내린 것처럼 손 없는 프레임을 1초 더 넣는다
    for (let t = last + 1 / 30; t < last + 1; t += 1 / 30) { const ev = seg.push(t, null); if (ev) events.push(ev); }
    expect(events.map(e => e.type)).toEqual(['start', 'end']);
    const kept = (events[1] as {frames: {time: number}[]}).frames.map(f => f.time);
    // 대기 구간은 빠지고, 손을 든 시점 직전 0.3초부터 끝까지는 빠짐없이 들어간다
    expect(kept[0]).toBeGreaterThanOrEqual(firstRaise - 0.31);
    expect(kept[0]).toBeLessThan(firstRaise);
    for (const t of raised) expect(kept).toContain(t);
  });
});
