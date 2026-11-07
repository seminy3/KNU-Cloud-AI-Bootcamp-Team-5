import {describe,it,expect} from 'vitest';
import {parseResponse,validateSequence} from './websocket';
import {normalize} from './keypointNormalizer';
describe('API contract',()=>{
  it('rejects malformed and empty sequences',()=>{expect(validateSequence([[[0,0]]])).toBe(false);expect(validateSequence(Array.from({length:30},()=>Array.from({length:49},()=>[0,0])))).toBe(false);});
  it('accepts finite 30×49×2 and rejects NaN',()=>{const sequence=Array.from({length:30},()=>Array.from({length:49},()=>[.1,.2]));expect(validateSequence(sequence)).toBe(true);sequence[0][0][0]=NaN;expect(validateSequence(sequence)).toBe(false);});
  it('keeps low probability candidates and server words',()=>{const response={type:'word',word:'병원',words:['나','병원'],count:2,candidates:[{name:'병원',prob:.04}]};expect(parseResponse(JSON.stringify(response))).toEqual(response);});
  it('accepts subtitle-only speech',()=>{expect(parseResponse(JSON.stringify({type:'speech',words:['배'],sentence:'배가 아파요.',audioUrl:null,needsConfirm:true})).type).toBe('speech');});
  it('rejects unknown messages',()=>{expect(()=>parseResponse('{"type":"joined"}')).toThrow();});
  it('requires a confirmed ML mapping',()=>{expect(()=>normalize({pose:[],left:[],right:[]},1280,720,{approved:false,points:[],shoulders:[0,1],sampleIntervalMs:33,missing:'reject-frame'})).toThrow();});
  it('normalizes in pixels and rejects missing shoulders',()=>{const frame={pose:[{x:.25,y:.5},{x:.75,y:.5}],left:[{x:.5,y:.75}],right:[]};const spec={approved:true,points:Array.from({length:49},()=>({source:'left' as const,index:0})),shoulders:[0,1] as [number,number],sampleIntervalMs:33,missing:'reject-frame' as const};expect(normalize(frame,1280,720,spec)?.[0]).toEqual([0,.28125]);expect(normalize({...frame,pose:[]},1280,720,spec)).toBeNull();});
});
