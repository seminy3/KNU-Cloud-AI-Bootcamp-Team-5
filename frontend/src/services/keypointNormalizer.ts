import type {Point} from '../types';
export interface Landmark {x:number;y:number;visibility?:number}
export interface Landmarks {pose:Landmark[];left:Landmark[];right:Landmark[]}
export interface InputSpec {approved:boolean;points:{source:keyof Landmarks;index:number}[];shoulders:[number,number];sampleIntervalMs:number;missing:'reject-frame'}
export function normalize(frame:Landmarks,width:number,height:number,spec:InputSpec):Point[]|null {
  if(!spec.approved||spec.points.length!==49||spec.sampleIntervalMs<=0)throw new Error('ML 팀이 확인한 INPUT_SPEC 설정이 필요합니다.');
  if(!frame.left.length&&!frame.right.length)return null;
  const [left,right]=spec.shoulders.map(i=>frame.pose[i]);
  if(!left||!right||(left.visibility??1)<0.5||(right.visibility??1)<0.5)return null;
  const scale=Math.hypot((left.x-right.x)*width,(left.y-right.y)*height);
  if(!Number.isFinite(scale)||scale<1)return null;
  const cx=(left.x+right.x)*width/2,cy=(left.y+right.y)*height/2;
  const points:Point[]=[];
  for(const ref of spec.points){const p=frame[ref.source][ref.index];if(!p||!Number.isFinite(p.x)||!Number.isFinite(p.y))return null;points.push([(p.x*width-cx)/scale,(p.y*height-cy)/scale]);}
  return points;
}
