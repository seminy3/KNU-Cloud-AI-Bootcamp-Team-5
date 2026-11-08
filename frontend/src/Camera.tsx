import {useEffect,useRef,useState} from 'react';
import {FilesetResolver,HandLandmarker,PoseLandmarker,DrawingUtils} from '@mediapipe/tasks-vision';
import {normalize,resample,assignHands,type TimedFrame} from './services/keypointNormalizer';
import {Segmenter} from './services/segmenter';
import type {Sequence} from './types';
export function Camera({disabled,onSequence}:{disabled:boolean;onSequence:(sequence:Sequence)=>void}){
  const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null);
  const cleanup=useRef<()=>void>(()=>{}),generation=useRef(0),capture=useRef(false),frames=useRef<TimedFrame[]>([]),started=useRef(0);
  const callback=useRef(onSequence);callback.current=onSequence;
  // 자동 모드: 손을 들면 녹화를 시작하고 내리면 인식한다(services/segmenter.ts)
  const segmenter=useRef(new Segmenter()),disabledRef=useRef(disabled),autoRef=useRef(true);disabledRef.current=disabled;
  const [auto,setAuto]=useState(true),[recording,setRecording]=useState(false);autoRef.current=auto;
  const [devices,setDevices]=useState<MediaDeviceInfo[]>([]),[device,setDevice]=useState(''),[ready,setReady]=useState(false),[loading,setLoading]=useState(false),[collecting,setCollecting]=useState(false),[count,setCount]=useState(0),[message,setMessage]=useState('C920을 연결하고 카메라 켜기를 눌러 주세요.'),[details,setDetails]=useState('');
  function stop(){generation.current++;cleanup.current();capture.current=false;setCollecting(false);setReady(false);setLoading(false);}
  useEffect(()=>()=>{generation.current++;cleanup.current();},[]);
  useEffect(()=>{if(disabled&&!autoRef.current){capture.current=false;setCollecting(false);}},[disabled]);
  function send(list:TimedFrame[]){
    try{const sequence=resample(list);setMessage(`동작 ${list.length}프레임을 30프레임으로 변환했습니다.`);callback.current(sequence);}catch(e){setMessage((e as Error).message);}
  }
  function finish(){
    if(autoRef.current){const ev=segmenter.current.finish();setRecording(false);if(ev?.type==='end')send(ev.frames);else if(ev?.type==='discard')setMessage(ev.reason);return;}
    capture.current=false;setCollecting(false);send(frames.current);
  }
  function arm(){frames.current=[];started.current=performance.now();setCount(0);segmenter.current.reset();setRecording(false);setMessage('');capture.current=true;setCollecting(true);}
  function disarm(){capture.current=false;frames.current=[];segmenter.current.reset();setRecording(false);setCollecting(false);}
  async function prepare(){
    stop();const token=generation.current;setLoading(true);setMessage('카메라와 관절 인식 모델을 준비하고 있습니다.');
    let stream:MediaStream|undefined,pose:PoseLandmarker|undefined,hands:HandLandmarker|undefined,raf=0;
    cleanup.current=()=>{cancelAnimationFrame(raf);stream?.getTracks().forEach(t=>t.stop());pose?.close();hands?.close();};
    try{
      stream=await navigator.mediaDevices.getUserMedia({video:{...(device?{deviceId:{exact:device}}:{}),width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30}},audio:false});
      if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());return;}
      const cameras=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');setDevices(cameras);
      if(!device){const c920=cameras.find(d=>/c920/i.test(d.label));if(c920&&stream.getVideoTracks()[0].getSettings().deviceId!==c920.deviceId){stream.getTracks().forEach(t=>t.stop());stream=await navigator.mediaDevices.getUserMedia({video:{deviceId:{exact:c920.deviceId},width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30}},audio:false});}}
      if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());return;}
      const track=stream.getVideoTracks()[0],settings=track.getSettings();setDevice(settings.deviceId||'');setDetails(`${track.label} · ${settings.width} × ${settings.height} · ${Math.round(settings.frameRate||0)}fps`);
      video.current!.srcObject=stream;await video.current!.play();
      const files=await FilesetResolver.forVisionTasks('/mediapipe/wasm');
      pose=await PoseLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:import.meta.env.VITE_POSE_MODEL_URL||'/models/pose_landmarker_full.task'},runningMode:'VIDEO',numPoses:1});
      hands=await HandLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:import.meta.env.VITE_HAND_MODEL_URL||'/models/hand_landmarker.task'},runningMode:'VIDEO',numHands:2});
      if(token!==generation.current){pose.close();hands.close();return;}
      setReady(true);setLoading(false);setMessage('수어 시작 → 한 단어 표현 → 완료 및 인식을 눌러 주세요.');let previous=-1;
      function tick(now:number){
        if(token!==generation.current)return;
        try{
          const v=video.current!,c=canvas.current!;
          if(v.readyState>=2&&v.currentTime!==previous){
            previous=v.currentTime;const p=pose!.detectForVideo(v,now),h=hands!.detectForVideo(v,now);
            c.width=v.videoWidth;c.height=v.videoHeight;const draw=new DrawingUtils(c.getContext('2d')!);
            for(const landmarks of p.landmarks)draw.drawConnectors(landmarks,PoseLandmarker.POSE_CONNECTIONS,{color:'#60a5fa',lineWidth:3});
            for(const landmarks of h.landmarks){draw.drawConnectors(landmarks,HandLandmarker.HAND_CONNECTIONS,{color:'#34d399',lineWidth:3});draw.drawLandmarks(landmarks,{color:'white',radius:2});}
            if(capture.current){
              const posePoints=p.landmarks[0]||[];
              const points=normalize({pose:posePoints,...assignHands(posePoints,h.landmarks,v.videoWidth,v.videoHeight)},v.videoWidth,v.videoHeight);
              if(autoRef.current){
                const seg=segmenter.current;
                // 앞 단어의 응답을 기다리는 동안은 받지 않고, 손을 한 번 내려야 다음 단어를 받는다
                if(disabledRef.current)seg.hold();
                else{const ev=seg.push(v.currentTime,points);setCount(seg.count);setRecording(seg.active);if(ev?.type==='end')send(ev.frames);else if(ev?.type==='discard')setMessage(ev.reason);}
              }else{
                if(points){frames.current.push({time:v.currentTime,points});setCount(frames.current.length);}
                if(now-started.current>15000){capture.current=false;setCollecting(false);frames.current=[];setMessage('15초가 지났습니다. 한 단어씩 다시 표현해 주세요.');}
              }
            }
          }
          raf=requestAnimationFrame(tick);
        }catch(e){stop();setMessage('관절 인식 오류: '+(e as Error).message);}
      }
      raf=requestAnimationFrame(tick);
    }catch(e){if(token===generation.current){stop();setMessage((e as Error).name==='NotAllowedError'?'브라우저의 카메라 권한을 허용해 주세요.':(e as Error).message);}}
  }
  return <><label htmlFor="camera-device">카메라 선택</label><select id="camera-device" value={device} disabled={loading||collecting} onChange={e=>{stop();setDevice(e.target.value);}}><option value="">자동 선택 (C920 우선)</option>{devices.map(d=><option key={d.deviceId} value={d.deviceId}>{d.label||'카메라'}</option>)}</select><p className="muted">{details}</p><div className="camera"><video ref={video} muted playsInline/><canvas ref={canvas}/>{!ready&&!loading&&<div className="camera-placeholder"><strong>카메라 미리보기</strong><span>얼굴과 양손이 보이도록 맞춰 주세요.</span></div>}</div><label className="auto-toggle"><input type="checkbox" checked={auto} disabled={collecting} onChange={e=>setAuto(e.target.checked)}/>손을 들고 내리면 자동으로 인식</label><p role="status">{collecting?(auto?(recording?`녹화 중 · 유효 ${count}프레임 · 손을 내리면 인식합니다.`:disabled?'인식 결과를 기다리고 있습니다.':'손을 들면 녹화를 시작하고, 내리면 인식합니다.'):`동작을 수집하고 있습니다. 유효 ${count}프레임`):message}</p>{collecting&&auto&&!recording&&message&&<p className="muted">{message}</p>}<div className="actions"><button disabled={loading||collecting} onClick={prepare}>{loading?'준비 중…':ready?'카메라 다시 연결':'카메라 켜기'}</button><button disabled={!ready||disabled||collecting} onClick={arm}>{auto?'자동 인식 시작':'수어 시작'}</button><button disabled={!collecting||(auto&&!recording)} onClick={finish}>완료 및 인식</button><button className="secondary" disabled={!collecting} onClick={disarm}>{auto?'자동 인식 끄기':'취소'}</button><button className="secondary" disabled={!ready&&!loading} onClick={stop}>카메라 끄기</button></div></>;
}
