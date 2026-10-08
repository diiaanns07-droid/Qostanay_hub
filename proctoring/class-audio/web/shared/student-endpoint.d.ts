export interface Indicators { micRequested: boolean; micLive: boolean; teacherSpeaking: boolean; sessionActive: boolean; teacherLevel: number }
export class StudentAudioEndpoint {
  constructor(options: { audioElement: HTMLAudioElement; send(message: Record<string, unknown>): void; onIndicators(ind: Indicators): void | Promise<void>; getUserMedia?(constraints: MediaStreamConstraints): Promise<MediaStream>; onLog?(event: { type: string; detail?: unknown }): void });
  mic: MediaStreamTrack | null;
  remote: MediaStreamTrack | null;
  pc: RTCPeerConnection | null;
  readonly micActive: boolean;
  _acquireMic(): Promise<void>;
  handleMessage(message: unknown): Promise<void>;
  transportLost(): void;
  stop(reason?: string): void;
}
