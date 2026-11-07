class PCMProcessor extends AudioWorkletProcessor {
  constructor(){super();this.samples=[];this.chunk=Math.round(sampleRate/10);}
  process(inputs){
    const input=inputs[0]?.[0];
    if(input)for(const value of input){this.samples.push(value);if(this.samples.length===this.chunk){const buffer=new ArrayBuffer(this.chunk*2);const view=new DataView(buffer);this.samples.forEach((sample,i)=>view.setInt16(i*2,Math.max(-1,Math.min(1,sample))*32767,true));this.port.postMessage(buffer,[buffer]);this.samples=[];}}
    return true;
  }
}
registerProcessor('pcm',PCMProcessor);
