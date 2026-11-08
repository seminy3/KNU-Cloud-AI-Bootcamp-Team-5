import {describe,it,expect} from 'vitest';
import {normalize,resample,assignHands,roundSequence,type Landmark} from './keypointNormalizer';
import {validateSequence,parseResponse} from './websocket';
import real from '../../tests/fixtures/golden_real.json';
import synthetic from '../../tests/fixtures/golden_synthetic.json';
const landmarks=(points:number[][]|null):Landmark[] => (points||[]).map(p=>({x:p[0],y:p[1]}));
for(const [name,golden] of [['real',real],['synthetic',synthetic]] as const){
  it(`matches ML golden ${name}`,()=>{
    const frames=golden.input.frames.flatMap(f=>{const points=normalize({pose:landmarks(f.pose),left:landmarks(f.left_hand),right:landmarks(f.right_hand)},golden.input.width,golden.input.height);return points?[{time:f.t,points}]:[];});
    const result=resample(frames);
    let maxError=0;
    result.forEach((f,i)=>f.forEach((p,j)=>p.forEach((v,k)=>{maxError=Math.max(maxError,Math.abs(v-golden.expected_keypoints[i][j][k]));})));
    expect(maxError).toBeLessThan(.001);
    expect(validateSequence(result)).toBe(true);
    expect(new TextEncoder().encode(JSON.stringify({action:'recognize',sessionId:'a'.repeat(128),keypoints:roundSequence(result)})).length).toBeLessThan(32768);
  });
}
describe('input guards',()=>{
  it('rejects short and invalid timelines',()=>{expect(()=>resample([])).toThrow();const points=Array.from({length:49},()=>[1,1] as [number,number]);expect(()=>resample(Array.from({length:5},()=>({time:1,points})))).toThrow();expect(()=>resample([0,1,2,1,3].map(time=>({time,points})))).toThrow();});
  it('assigns two hands by wrist distance',()=>{const pose=Array.from({length:33},()=>({x:0,y:0}));pose[15]={x:.8,y:.5};pose[16]={x:.2,y:.5};const a=[{x:.2,y:.5}],b=[{x:.8,y:.5}];expect(assignHands(pose,[a,b],1280,720)).toEqual({left:b,right:a});});
  it('rejects NaN and accepts low candidate probability',()=>{expect(validateSequence([[[NaN,0]]])).toBe(false);expect(parseResponse(JSON.stringify({type:'word',word:'나',words:['나'],count:1,candidates:[{name:'나',prob:.04}]})).type).toBe('word');});
});
