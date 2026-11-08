import assert from 'node:assert/strict';
import {WebSocket} from 'ws';
const socket=new WebSocket('ws://localhost:8765');
await new Promise((resolve,reject)=>{socket.once('open',resolve);socket.once('error',reject);});
const sessionId=crypto.randomUUID();
async function request(data){const response=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Timeout')),3000);socket.once('message',raw=>{clearTimeout(timer);resolve(JSON.parse(raw.toString()));});});socket.send(JSON.stringify({...data,sessionId}));return response;}
try{
  const keypoints=Array.from({length:30},()=>Array.from({length:49},()=>[.1,.2]));
  assert.deepEqual((await request({action:'recognize',keypoints})).words,['나']);
  assert.deepEqual((await request({action:'recognize',keypoints})).words,['나','병원']);
  const speech=await request({action:'finalize'});assert.equal(speech.type,'speech');assert.equal(speech.needsConfirm,false);
  assert.equal((await request({action:'finalize'})).stage,'sentence');
  assert.deepEqual((await request({action:'finalize',words:['머리','아프다']})).words,['머리','아프다']);
  assert.equal((await request({action:'doctorUtterance',text:'어디가 아프세요?'})).text,'어디가 아프세요?');
  assert.equal((await request({action:'recognize',keypoints:[]})).type,'error');
  // 두 화면 전달: 환자·의료진 연결이 같은 대화 코드로 join하면 서로의 메시지를 받는다
  const doctor=new WebSocket('ws://localhost:8765');await new Promise(r=>doctor.once('open',r));
  const next=ws=>new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('Timeout')),3000);ws.once('message',raw=>{clearTimeout(t);resolve(JSON.parse(raw.toString()));});});
  assert.deepEqual((await request({action:'join',role:'patient'})).roles,['patient']);
  const both=Promise.all([next(socket),next(doctor)]);doctor.send(JSON.stringify({action:'join',sessionId,role:'doctor'}));
  for(const m of await both)assert.deepEqual(m.roles,['doctor','patient']);
  const toDoctor=next(doctor);await request({action:'finalize',words:['머리']});assert.equal((await toDoctor).role,'patient');
  const toPatient=next(socket);doctor.send(JSON.stringify({action:'doctorUtterance',sessionId,text:'언제부터요?'}));await next(doctor);assert.deepEqual(await toPatient,{type:'relay',role:'doctor',text:'언제부터요?'});
  const left=next(socket);doctor.close();assert.deepEqual((await left).roles,['patient']);
  console.log('PASS: real WebSocket recognize, accumulation, finalize, buffer reset, transcript, error, join, relay');
}finally{socket.close();}
