import type {Point, Sequence} from '../types';
import definitions from '../ml/points.json';
export interface Landmark {x:number;y:number;visibility?:number}
export interface Landmarks {pose:Landmark[];left:Landmark[];right:Landmark[]}
export interface TimedFrame {time:number;points:Point[]}
export function normalize(frame:Landmarks,width:number,height:number):Point[]|null {
  if(!frame.pose.length||(!frame.left.length&&!frame.right.length))return null;
  if(!(width>0&&height>0))throw new Error('영상 크기를 확인해 주세요.');
  for(const [name,count] of [['pose',33],['left',21],['right',21]] as const){
    const points=frame[name];
    if((points.length&&points.length!==count)||!points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)))throw new Error('관절 좌표가 올바르지 않습니다.');
  }
  const left=frame.pose[11],right=frame.pose[12];
  const scale=Math.hypot((left.x-right.x)*width,(left.y-right.y)*height);
  if(scale<width*.001)return null;
  const cx=(left.x+right.x)*width/2,cy=(left.y+right.y)*height/2;
  return definitions.map(({source,index}):Point=>{
    const points=frame[source as keyof Landmarks];
    if(source!=='pose'&&!points.length)return [0,0];
    const p=points[index];return [(p.x*width-cx)/scale,(p.y*height-cy)/scale];
  });
}
export function assignHands(pose:Landmark[],hands:Landmark[][],width:number,height:number):Pick<Landmarks,'left'|'right'>{
  if(!pose[15]||!pose[16]||!hands.length)return {left:[],right:[]};
  const distance=(hand:Landmark[],wrist:Landmark)=>Math.hypot((hand[0].x-wrist.x)*width,(hand[0].y-wrist.y)*height);
  const first=hands[0],second=hands[1];
  if(!second)return distance(first,pose[15])<=distance(first,pose[16])?{left:first,right:[]}:{left:[],right:first};
  return distance(first,pose[15])+distance(second,pose[16])<=distance(first,pose[16])+distance(second,pose[15])?{left:first,right:second}:{left:second,right:first};
}
export function resample(frames:TimedFrame[]):Sequence{
  if(frames.length<5)throw new Error('유효한 프레임이 부족합니다. 수어를 다시 표현해 주세요.');
  if(frames.some((f,i)=>!Number.isFinite(f.time)||(i>0&&f.time<frames[i-1].time)))throw new Error('영상 시각이 올바르지 않습니다.');
  const start=frames[0].time,duration=frames.at(-1)!.time-start;
  if(duration<=0)throw new Error('동작 구간이 너무 짧습니다.');
  let upper=1;
  return Array.from({length:30},(_,i)=>{
    const time=start+i*duration/29;
    while(upper<frames.length-1&&frames[upper].time<time)upper++;
    const a=frames[upper-1],b=frames[upper];
    const w=Math.max(0,Math.min(1,(time-a.time)/Math.max(b.time-a.time,1e-12)));
    return a.points.map((p,j):Point=>{
      const q=b.points[j];
      if((p[0]===0&&p[1]===0)||(q[0]===0&&q[1]===0))return [...(w>.5?q:p)];
      return [p[0]*(1-w)+q[0]*w,p[1]*(1-w)+q[1]*w];
    });
  });
}
export function roundSequence(sequence:Sequence):Sequence{
  return sequence.map(frame=>frame.map(([x,y])=>[Math.round(x*1e4)/1e4,Math.round(y*1e4)/1e4]));
}
