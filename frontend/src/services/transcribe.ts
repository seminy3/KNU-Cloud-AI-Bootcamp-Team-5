import {TranscribeStreamingClient,StartStreamTranscriptionCommand} from '@aws-sdk/client-transcribe-streaming';
import {fromCognitoIdentityPool} from '@aws-sdk/credential-provider-cognito-identity';
export async function transcribe(signal:AbortSignal,onText:(id:string,text:string,partial:boolean)=>void,onReady:()=>void){
  const identityPoolId=import.meta.env.VITE_COGNITO_IDENTITY_POOL_ID;
  if(!identityPoolId)throw new Error('음성 인식 연결 설정이 필요합니다.');
  const region=import.meta.env.VITE_AWS_REGION||'ap-northeast-2';
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true},video:false});
  if(signal.aborted){stream.getTracks().forEach(t=>t.stop());return;}
  const context=new AudioContext();
  const client=new TranscribeStreamingClient({region,credentials:fromCognitoIdentityPool({clientConfig:{region},identityPoolId})});
  let wake:(()=>void)|undefined;const queue:Uint8Array[]=[];let node:AudioWorkletNode|undefined;
  const stop=()=>{stream.getTracks().forEach(t=>t.stop());wake?.();};
  signal.addEventListener('abort',stop,{once:true});
  try {
    await context.audioWorklet.addModule('/pcm-worklet.js');
    if(signal.aborted)return;
    node=new AudioWorkletNode(context,'pcm');
    node.port.onmessage=e=>{queue.push(new Uint8Array(e.data));if(queue.length>50){queue.shift();}wake?.();};
    context.createMediaStreamSource(stream).connect(node);node.connect(context.destination);await context.resume();
    async function* audio(){while(!signal.aborted){if(!queue.length)await new Promise<void>(resolve=>{wake=resolve;});while(queue.length&&!signal.aborted)yield {AudioEvent:{AudioChunk:queue.shift()!}};}}
    onReady();
    const result=await client.send(new StartStreamTranscriptionCommand({LanguageCode:'ko-KR',MediaEncoding:'pcm',MediaSampleRateHertz:context.sampleRate,AudioStream:audio()}),{abortSignal:signal});
    for await(const event of result.TranscriptResultStream??[]){for(const r of event.TranscriptEvent?.Transcript?.Results??[]){const text=r.Alternatives?.[0]?.Transcript;if(text&&r.ResultId)onText(r.ResultId,text,!!r.IsPartial);}}
  }finally{signal.removeEventListener('abort',stop);stop();node?.disconnect();await context.close();client.destroy();}
}
