import type {Request, Response, Status, Sequence} from '../types';
export function validateSequence(value:unknown): value is Sequence {
  return Array.isArray(value) && value.length===30 && value.every(frame=>Array.isArray(frame)&&frame.length===49&&frame.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))) && value.some(frame=>frame.some((p:number[])=>p.some((n:number)=>n!==0)));
}
export function parseResponse(raw:string):Response {
  const x=JSON.parse(raw);
  const strings=(v:unknown)=>Array.isArray(v)&&v.every(s=>typeof s==='string');
  if(x?.type==='word' && typeof x.word==='string' && strings(x.words) && x.count===x.words.length && Array.isArray(x.candidates) && x.candidates.every((c:any)=>typeof c.name==='string'&&Number.isFinite(c.prob))) return x;
  if(x?.type==='speech' && strings(x.words) && typeof x.sentence==='string' && (x.audioUrl===null||typeof x.audioUrl==='string') && typeof x.needsConfirm==='boolean') return x;
  if(x?.type==='transcript' && typeof x.text==='string') return x;
  if(x?.type==='error' && ['recognize','sentence','speech','transcript'].includes(x.stage) && typeof x.message==='string' && typeof x.retryable==='boolean') return x;
  throw new Error('서버 응답 형식을 확인할 수 없습니다.');
}
export class Connection {
  private socket?:WebSocket;
  private timer?:ReturnType<typeof setTimeout>;
  private attempts=0;
  private closed=false;
  constructor(private url:string,private status:(s:Status)=>void,private message:(r:Response)=>void,private error:(s:string)=>void){}
  connect(){
    this.closed=false;
    this.status(this.attempts?'reconnecting':'connecting');
    try {this.socket=new WebSocket(this.url);} catch {this.status('error');this.error('연결 주소를 확인해 주세요.');return;}
    const socket=this.socket;
    socket.onopen=()=>{if(this.closed)return;this.attempts=0;this.status('connected');};
    socket.onmessage=e=>{if(this.closed)return;try{this.message(parseResponse(e.data));}catch{this.error('서버 응답 형식을 확인할 수 없습니다.');}};
    socket.onerror=()=>this.error('통역 서버 연결을 확인해 주세요.');
    socket.onclose=()=>{if(this.closed)return;this.status('reconnecting');this.timer=setTimeout(()=>this.connect(),Math.min(1000*2**this.attempts++,15000));};
  }
  send(request:Request){
    if(this.socket?.readyState!==WebSocket.OPEN)throw new Error('서버에 연결된 뒤 다시 시도해 주세요.');
    if(request.action==='recognize'&&!validateSequence(request.keypoints))throw new Error('좌표 데이터가 올바르지 않습니다.');
    const payload=JSON.stringify(request);
    if(new TextEncoder().encode(payload).length>32768)throw new Error('좌표 요청이 32KB를 초과합니다. 백엔드와 전송 규격 협의가 필요합니다.');
    this.socket.send(payload);
  }
  close(){this.closed=true;clearTimeout(this.timer);if(this.socket){this.socket.onclose=null;this.socket.close();}this.status('disconnected');}
}

