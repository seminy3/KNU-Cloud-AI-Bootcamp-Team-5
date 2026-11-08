import type {Point} from '../types';
import type {TimedFrame} from './keypointNormalizer';

// 손을 들고 내리는 동작으로 한 단어 구간을 자르는 규칙.
// 좌표는 normalize() 결과(어깨 중심이 원점, 어깨 너비가 1, y는 아래로 증가)다.
// 값은 docs/ml/FRONT_INPUT.md의 "단어 구간 자르기" 측정을 참고한 초기값이고, 실제 웹캠으로는 아직 조정하지 않았다.
//  - 앞부분이 잘리면 정확도가 더 떨어지므로 시작 직전 preRollSeconds를 함께 넣는다
//  - 끝은 조금 일찍 끊겨도 영향이 작아 손을 내린 뒤 endSeconds만 기다린다
export const SEGMENT = {raiseY: 1.2, startFrames: 3, endSeconds: 0.5, preRollSeconds: 0.3, tailSeconds: 0.1, minSeconds: 0.3, minFrames: 5, maxSeconds: 15};

// points.json 순서에서 왼손·오른손의 손목(손 0번 점)
const LEFT_WRIST = 7, RIGHT_WRIST = 28;
const present = (p: Point) => p[0] !== 0 || p[1] !== 0;

/** 검출된 손 중 하나라도 손목이 raiseY보다 위(어깨 쪽)에 있으면 손을 든 것으로 본다. */
export function handsUp(points: Point[] | null, raiseY = SEGMENT.raiseY): boolean {
  if (!points) return false;
  return [LEFT_WRIST, RIGHT_WRIST].some(i => present(points[i]) && points[i][1] < raiseY);
}

export type SegmentEvent = {type: 'start'} | {type: 'end'; frames: TimedFrame[]} | {type: 'discard'; reason: string} | null;

export class Segmenter {
  private buffer: TimedFrame[] = [];
  private frames: TimedFrame[] = [];
  private recording = false;
  private upRun = 0;
  private lastUp = 0;
  private firstUp = 0;
  private needsDown = false;
  constructor(private cfg = SEGMENT) {}
  get active() { return this.recording; }
  get count() { return this.frames.length; }

  reset() { this.buffer = []; this.frames = []; this.recording = false; this.upRun = 0; this.needsDown = false; }

  /** 응답을 기다리는 동안처럼 받으면 안 될 때. 녹화 중이던 것은 버리고, 손을 한 번 내려야 다시 시작한다. */
  hold() { this.buffer = []; this.frames = []; this.recording = false; this.upRun = 0; this.needsDown = true; }

  /** 프레임 하나를 넣는다. points가 null이면 손이 검출되지 않은 프레임이다. */
  push(time: number, points: Point[] | null): SegmentEvent {
    const up = handsUp(points, this.cfg.raiseY);
    if (!this.recording) {
      if (points) this.buffer.push({time, points});
      this.buffer = this.buffer.filter(f => time - f.time <= this.cfg.preRollSeconds);
      if (this.needsDown) {
        if (!up) this.needsDown = false;
        return null;
      }
      this.upRun = up ? this.upRun + 1 : 0;
      if (this.upRun === 1) this.firstUp = time;
      if (this.upRun < this.cfg.startFrames) return null;
      this.recording = true; this.frames = [...this.buffer]; this.buffer = []; this.lastUp = time; this.upRun = 0;
      return {type: 'start'};
    }
    if (points) this.frames.push({time, points});
    if (up) this.lastUp = time;
    if (this.frames.length && time - this.frames[0].time > this.cfg.maxSeconds) {
      this.hold();
      return {type: 'discard', reason: '15초가 지났습니다. 한 단어씩 다시 표현해 주세요.'};
    }
    return time - this.lastUp >= this.cfg.endSeconds ? this.finish() : null;
  }

  /** 지금까지의 구간을 끝낸다(손을 내렸을 때 또는 '완료 및 인식' 버튼). */
  finish(): SegmentEvent {
    if (!this.recording) return null;
    const end = this.lastUp + this.cfg.tailSeconds;
    const frames = this.frames.filter(f => f.time <= end);
    this.hold();
    // 짧은 동작 판정은 시작 직전 여유를 빼고 손을 든 시간으로 한다
    if (frames.length < this.cfg.minFrames || this.lastUp - this.firstUp < this.cfg.minSeconds) {
      return {type: 'discard', reason: '동작이 너무 짧습니다. 한 단어를 끝까지 표현해 주세요.'};
    }
    return {type: 'end', frames};
  }
}
