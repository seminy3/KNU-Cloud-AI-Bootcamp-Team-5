export type Role = 'patient' | 'doctor';
export type Point = [number, number];
export type Sequence = Point[][];
export type Status = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'error';
export type Request = {action:'recognize';sessionId:string;keypoints:Sequence} | {action:'finalize';sessionId:string;words:string[]} | {action:'doctorUtterance';sessionId:string;text:string} | {action:'join';sessionId:string;role:Role};
export type Response = {type:'word';word:string;words:string[];count:number;candidates:{name:string;prob:number}[]} | {type:'speech';words:string[];sentence:string;audioUrl:string|null;needsConfirm:boolean} | {type:'transcript';text:string} | {type:'error';stage:'recognize'|'sentence'|'speech'|'transcript'|'join';message:string;retryable:boolean} | {type:'presence';roles:Role[]} | {type:'relay';role:Role;text:string;audioUrl?:string|null;needsConfirm?:boolean};
export interface Message {id:string;role:Role;text:string;time:number;needsConfirm?:boolean;audioUrl?:string|null}
