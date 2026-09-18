import type { LiveChannel } from "./types.js";
import { CHANNEL_BYTE } from "./types.js";
import { PCM_WORKLET_SOURCE } from "./worklet-source.js";

/** Prefix a PCM16LE mono 16 kHz chunk with the channel byte the live WS expects. */
export function framePcm(channel: LiveChannel, pcm: Int16Array): Uint8Array {
  const frame = new Uint8Array(1 + pcm.byteLength);
  frame[0] = CHANNEL_BYTE[channel];
  frame.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength), 1);
  return frame;
}

export interface MicHandle {
  stop: () => void;
}

/**
 * Browser-only: getUserMedia → 16 kHz mono PCM16 chunks (~200ms).
 * Pass each chunk to `CorellaLive.sendPcm("me", pcm)`.
 */
export async function startMic(onChunk: (pcm: Int16Array) => void): Promise<MicHandle> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices) {
    throw new Error("startMic() is only available in a browser");
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const audioContext = new AudioContext();
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  const blob = new Blob([PCM_WORKLET_SOURCE], { type: "application/javascript" });
  const url = URL.createObjectURL(blob);
  try {
    await audioContext.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }

  const source = audioContext.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(audioContext, "pcm-worklet", {
    processorOptions: { targetSampleRate: 16000, chunkMs: 200 },
  });
  worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    onChunk(new Int16Array(event.data));
  };
  source.connect(worklet);
  const silence = audioContext.createGain();
  silence.gain.value = 0;
  worklet.connect(silence);
  silence.connect(audioContext.destination);

  return {
    stop: () => {
      source.disconnect();
      worklet.disconnect();
      silence.disconnect();
      void audioContext.close();
      stream.getTracks().forEach((t) => t.stop());
    },
  };
}
