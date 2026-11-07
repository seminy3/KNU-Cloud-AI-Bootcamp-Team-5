import {WebSocketServer} from 'ws';
const server=new WebSocketServer({port:8765,host:'127.0.0.1'});
const sessions=new Map();
server.on('connection',socket=>socket.on('message',raw=>{
  let request;
  try{
    request=JSON.parse(raw.toString());
    const id=request.sessionId;
    const fail=(message,stage='recognize')=>socket.send(JSON.stringify({type:'error',stage,message,retryable:true}));
    if(typeof id!=='string'||!id)return fail('세션 코드가 필요합니다.');
    const state=sessions.get(id)||{words:[],index:0};sessions.set(id,state);
    if(request.action==='recognize'){
      const kp=request.keypoints;
      if(!Array.isArray(kp)||kp.length!==30||!kp.every(f=>Array.isArray(f)&&f.length===49&&f.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))))return fail('좌표 형식을 확인해 주세요.');
      const word=['나','병원','가다','아프다','머리'][state.index++%5];state.words.push(word);
      socket.send(JSON.stringify({type:'word',word,candidates:[{name:word,prob:.62}],words:state.words,count:state.words.length}));
    }else if(request.action==='finalize'){
      if(!state.words.length)return fail('변환할 단어가 없습니다.','sentence');
      socket.send(JSON.stringify({type:'speech',words:state.words,sentence:'저 병원에 가려고요',audioUrl:'http://localhost:8765/mock-audio.mp3',needsConfirm:false}));state.words=[];
    }else if(request.action==='doctorUtterance'){
      if(typeof request.text!=='string'||!request.text.trim())return fail('저장할 문장이 없습니다.','transcript');
      socket.send(JSON.stringify({type:'transcript',text:request.text}));
    }else fail('지원하지 않는 요청입니다.');
  }catch{socket.send(JSON.stringify({type:'error',stage:'recognize',message:'JSON 요청을 확인해 주세요.',retryable:true}));}
}));
console.log('SignMed contract mock listening on ws://localhost:8765');
