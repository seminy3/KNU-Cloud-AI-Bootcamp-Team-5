import type {Message} from '../types';
export interface PeerTransport {send(message:Message):boolean;close():void}
export function createPeer(sessionId:string,receive:(message:Message)=>void):PeerTransport {
  if(import.meta.env.VITE_MOCK_PEER!=='true')return {send:()=>false,close:()=>{}};
  const channel=new BroadcastChannel('signmed-mock-'+sessionId);
  channel.onmessage=e=>{const m=e.data;if(m&&typeof m.id==='string'&&typeof m.text==='string'&&['patient','doctor'].includes(m.role))receive(m);};
  return {send:message=>{channel.postMessage(message);return true;},close:()=>channel.close()};
}
