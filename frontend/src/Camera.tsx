import {useEffect,useRef,useState} from 'react';
import {FilesetResolver,HandLandmarker,PoseLandmarker,DrawingUtils} from '@mediapipe/tasks-vision';
import {normalize,type InputSpec} from './services/keypointNormalizer';
import type {Sequence} from './types';
export function Camera({disabled,onSequence}:{disabled:boolean;onSequence:(sequence:Sequence)=>void}){
  const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null);
  const stop=useRef<()=>void>(()=>{});
  const [ready,setReady]=useState(false),[collecting,setCollecting]=useState(false),[progress,setProgress]=useState(0),[error,setError]=useState('');
  const capture=useRef(false),spec=useRef<InputSpec|null>(null),frames=useRef<Sequence>([]);
  const mounted=useRef(true),starting=useRef(false);
  useEffect(()=>()=>{mounted.current=false;stop.current();},[]);
  useEffect(()=>{if(disabled){capture.current=false;setCollecting(false);}},[disabled]);
  async function prepare(){
    if(starting.current)return;starting.current=true;setError('카메라를 준비하고 있습니다.');let stream:MediaStream|undefined,pose:PoseLandmarker|undefined,hands:HandLandmarker|undefined,raf=0,cancelled=false;
    stop.current=()=>{cancelled=true;cancelAnimationFrame(raf);stream?.getTracks().forEach(t=>t.stop());pose?.close();hands?.close();};
    try{
      stream=await navigator.mediaDevices.getUserMedia({video:{width:1280,height:720},audio:false});
      if(cancelled||!mounted.current){stream.getTracks().forEach(t=>t.stop());return;}
      video.current!.srcObject=stream;await video.current!.play();setReady(true);
      const config=await fetch('/input-spec.json');if(config.ok)spec.current=await config.json();
      if(!import.meta.env.VITE_POSE_MODEL_URL||!import.meta.env.VITE_HAND_MODEL_URL)throw new Error('카메라가 준비되었습니다. 관절 인식 모델 파일 설정이 필요합니다.');
      const files=await FilesetResolver.forVisionTasks('/mediapipe/wasm');
      pose=await PoseLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:import.meta.env.VITE_POSE_MODEL_URL},runningMode:'VIDEO',numPoses:1});
      hands=await HandLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:import.meta.env.VITE_HAND_MODEL_URL},runningMode:'VIDEO',numHands:2});
      if(cancelled){pose.close();hands.close();return;}
      setError(spec.current?.approved?'':'모델 입력 규격 확인이 필요합니다. 카메라 미리보기를 사용할 수 있습니다.');
      let last=0,lastVideo=-1,started=0;
      const tick=(now:number)=>{
        if(cancelled)return;
        const v=video.current!,c=canvas.current!;
        if(v.currentTime!==lastVideo&&v.readyState>=2){
          lastVideo=v.currentTime;const p=pose!.detectForVideo(v,now),h=hands!.detectForVideo(v,now);
          c.width=v.videoWidth;c.height=v.videoHeight;const ctx=c.getContext('2d')!;const drawing=new DrawingUtils(ctx);
          for(const landmarks of p.landmarks)drawing.drawConnectors(landmarks,PoseLandmarker.POSE_CONNECTIONS,{color:'#60a5fa',lineWidth:3});
          for(const landmarks of h.landmarks){drawing.drawConnectors(landmarks,HandLandmarker.HAND_CONNECTIONS,{color:'#34d399',lineWidth:3});drawing.drawLandmarks(landmarks,{color:'#ffffff',radius:2});}
          if(capture.current&&spec.current&&now-last>=spec.current.sampleIntervalMs){
            if(!started)started=now;last=now;
            const left=h.handedness.findIndex(a=>a[0]?.categoryName==='Left'),right=h.handedness.findIndex(a=>a[0]?.categoryName==='Right');
            const frame=normalize({pose:p.landmarks[0]||[],left:h.landmarks[left]||[],right:h.landmarks[right]||[]},v.videoWidth,v.videoHeight,spec.current);
            if(frame){frames.current.push(frame);setProgress(frames.current.length);}
            if(frames.current.length===30){capture.current=false;setCollecting(false);onSequence(frames.current);started=0;}
            else if(now-started>15000){capture.current=false;setCollecting(false);setError('수어를 다시 표현해 주세요. 얼굴과 양손을 화면 안에 맞춰 주세요.');started=0;}
          }
        }
        raf=requestAnimationFrame(tick);
      };raf=requestAnimationFrame(tick);
    }catch(e){if(mounted.current)setError((e as Error).name==='NotAllowedError'?'카메라 권한을 허용한 뒤 다시 시도해 주세요.':(e as Error).message);}finally{starting.current=false;}
  }
  function collect(){if(!spec.current?.approved){setError('모델 입력 규격 확인이 필요합니다.');return;}frames.current=[];setProgress(0);capture.current=true;setCollecting(true);}
  return <><div className="camera"><video ref={video} muted playsInline/><canvas ref={canvas}/>{!ready&&<div className="camera-placeholder">▣<strong>카메라 미리보기</strong><span>얼굴과 양손이 화면 안에 들어오도록 맞춰 주세요.</span></div>}</div><p role="status">{collecting?`수어를 인식하고 있습니다. ${progress} / 30`:error||'한 단어씩 수어를 표현해 주세요.'}</p><div className="actions"><button onClick={ready?collect:prepare} disabled={disabled||collecting}>{ready?'수어 인식 시작':'카메라 켜기'}</button><button className="secondary" disabled={!collecting} onClick={()=>{capture.current=false;setCollecting(false);frames.current=[];}}>인식 중지</button><button className="secondary" disabled={!ready} onClick={()=>{stop.current();setReady(false);capture.current=false;setCollecting(false);}}>카메라 끄기</button></div></>;
}
