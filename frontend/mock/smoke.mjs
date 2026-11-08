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
  assert.equal((await request({action:'doctorUtterance',text:'어디가 아프세요?'})).text,'어디가 아프세요?');
  assert.equal((await request({action:'recognize',keypoints:[]})).type,'error');
  console.log('PASS: real WebSocket recognize, accumulation, finalize, buffer reset, transcript, error');
}finally{socket.close();}
