import { installClassAudio } from "../../src/lib/classAudio";
const state = (window as any).audioTest = { calls: [] as any[], tracks: [] as MediaStreamTrack[], failure: "", pcs: [] as RTCPeerConnection[] };
const realMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (constraints) => {
  const banner = document.querySelector<HTMLElement>("[data-testid=class-audio-notice]")!;
  state.calls.push({ visible: !banner.hidden, text: banner.textContent, constraints, visibility: document.visibilityState });
  if (state.failure) throw new DOMException("Synthetic failure", state.failure);
  const stream = await realMedia(constraints);
  state.tracks.push(...stream.getTracks());
  return stream;
};
const Peer = RTCPeerConnection;
(window as any).RTCPeerConnection = class extends Peer { constructor(config?: RTCConfiguration) { super(config); state.pcs.push(this); } };
installClassAudio({ subscribeEvents: (window as any).audioFixture.subscribe, onShellState: () => () => {} } as any);
