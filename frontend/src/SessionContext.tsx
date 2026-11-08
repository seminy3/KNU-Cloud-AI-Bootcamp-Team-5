import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {Connection} from './services/websocket';
import {createPeer,type PeerTransport} from './services/peer';
import type {Message,Request,Response,Role,Status} from './types';
function useSessionState(){
  const [sessionId,setSessionId]=useState(()=>sessionStorage.getItem('signmed-session')||'');
  const [status,setStatus]=useState<Status>('disconnected');
  // 화면에서 고친 단어 목록이 기준이다. finalize 때 이 목록을 보내므로 서버 버퍼의 1위 단어는 쓰지 않는다.
  const [items,setItems]=useState<{word:string;candidates:string[]}[]>([]);
  const words=items.map(i=>i.word);
  const [response,setResponse]=useState<Response|null>(null);
  const [messages,setMessages]=useState<Message[]>([]);
  const [error,setError]=useState('');
  const [pending,setPending]=useState('');
  const [uncertain,setUncertain]=useState(false);
  // 이 화면의 역할과, 같은 대화 코드에 접속한 역할 목록(서버 presence)
  const [role,setRole]=useState<Role|null>(null);
  const [peers,setPeers]=useState<Role[]>([]);
  const connection=useRef<Connection|null>(null),peer=useRef<PeerTransport|null>(null),pendingRef=useRef('');
  const timer=useRef<ReturnType<typeof setTimeout>>(undefined);
  const add=(message:Message)=>setMessages(all=>all.some(m=>m.id===message.id)?all:[...all,message]);
  useEffect(()=>{
    if(!sessionId)return;
    sessionStorage.setItem('signmed-session',sessionId);
    peer.current=createPeer(sessionId,add);
    const client=new Connection(import.meta.env.VITE_WS_URL||'ws://localhost:8765',s=>{
      setStatus(s);
      if(s==='reconnecting'&&pendingRef.current){setUncertain(true);setError('요청 처리 여부를 확인할 수 없습니다. 중복 전송을 막기 위해 새 대화를 시작해 주세요.');}
    },r=>{
      // 상대 화면 관련 메시지는 내 요청의 응답이 아니므로 response를 바꾸지 않는다
      if(r.type==='presence'){setPeers(r.roles);return;}
      if(r.type==='relay'){add({id:crypto.randomUUID(),role:r.role,text:r.text,time:Date.now(),needsConfirm:r.needsConfirm,audioUrl:r.audioUrl});return;}
      setResponse(r);
      const expected=pendingRef.current;
      if(r.type==='word'){const item={word:r.word,candidates:r.candidates.map(c=>c.name)};setItems(all=>[...all,item]);}
      if(r.type==='speech'){
        setItems([]);const message:Message={id:crypto.randomUUID(),role:'patient',text:r.sentence,time:Date.now(),needsConfirm:r.needsConfirm};add(message);peer.current?.send(message);
      }
      if(r.type==='error')setError(r.message+(r.retryable?' 다시 시도할 수 있습니다.':' 새 대화를 시작해 주세요.'));
      if((expected==='recognize'&&(r.type==='word'||r.type==='error'&&r.stage==='recognize'))||(expected==='finalize'&&(r.type==='speech'||r.type==='error'&&['sentence','speech'].includes(r.stage)))){
        clearTimeout(timer.current);pendingRef.current='';setPending('');
        if(r.type==='error'&&!r.retryable)setUncertain(true);
      }
    },setError);
    connection.current=client;client.connect();
    return ()=>{client.close();peer.current?.close();clearTimeout(timer.current);pendingRef.current='';setPeers([]);};
  },[sessionId]);
  // 연결될 때마다(재연결 포함) 대화 코드와 역할을 서버에 등록한다
  useEffect(()=>{
    if(status!=='connected'||!sessionId||!role)return;
    try{connection.current?.send({action:'join',sessionId,role});}catch(e){setError((e as Error).message);}
  },[status,sessionId,role]);
  function begin(id:string=crypto.randomUUID()){setItems([]);setMessages([]);setResponse(null);setError('');setUncertain(false);setPending('');pendingRef.current='';setSessionId(id);}
  function end(){setSessionId('');sessionStorage.removeItem('signmed-session');setItems([]);setMessages([]);setResponse(null);}
  function send(request:Request){
    try{
      if(request.action!=='doctorUtterance'&&(pendingRef.current||uncertain))throw new Error('이전 요청을 확인 중입니다.');
      connection.current?.send(request);
      if(!connection.current)throw new Error('연결을 먼저 시작해 주세요.');
      if(request.action!=='doctorUtterance'){
        pendingRef.current=request.action;setPending(request.action);setError('');
        timer.current=setTimeout(()=>{setUncertain(true);setError('응답 대기 시간이 지났습니다. 처리 여부가 불확실하므로 새 대화를 시작해 주세요.');},45000);
      }
      return true;
    }catch(e){setError((e as Error).message);return false;}
  }
  function doctor(text:string){const message:Message={id:crypto.randomUUID(),role:'doctor',text,time:Date.now()};add(message);peer.current?.send(message);send({action:'doctorUtterance',sessionId,text});}
  const replaceWord=(index:number,word:string)=>setItems(all=>all.map((item,i)=>i===index?{...item,word}:item));
  const removeWord=(index:number)=>setItems(all=>all.filter((_,i)=>i!==index));
  const clearWords=()=>setItems([]);
  const finalize=()=>words.length>0&&send({action:'finalize',sessionId,words});
  return {sessionId,status,role,setRole,peers,words,items,replaceWord,removeWord,clearWords,finalize,response,messages,error,setError,pending,uncertain,begin,end,send,doctor};
}
const SessionContext=createContext<ReturnType<typeof useSessionState>|null>(null);
export function SessionProvider({children}:{children:ReactNode}){const state=useSessionState();return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;}
export function useSession(){const state=useContext(SessionContext);if(!state)throw new Error('SessionProvider required');return state;}

