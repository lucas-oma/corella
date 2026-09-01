// AudioWorkletProcessor: downmixes whatever the browser gives us (native
// sample rate, N channels) to mono, resamples it down to 16kHz via simple
// streaming linear interpolation (fine for speech/VAD, no need for
// anything fancier here), and posts ~200ms Int16 PCM16LE chunks back to
// the main thread as transferable ArrayBuffers.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = options.processorOptions || {};
    this.targetRate = opts.targetSampleRate || 16000;
    this.chunkMs = opts.chunkMs || 200;
    this.ratio = sampleRate / this.targetRate; // `sampleRate` is a worklet-global
    this.samplesPerChunk = Math.round((this.targetRate * this.chunkMs) / 1000);

    this.inputBuffer = new Float32Array(0);
    this.readPos = 0; // fractional index into inputBuffer, native-rate space
    this.outputBuffer = [];
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || input[0].length === 0) return true;

    const channelCount = input.length;
    const frameCount = input[0].length;
    const mono = new Float32Array(frameCount);
    for (let ch = 0; ch < channelCount; ch++) {
      const data = input[ch];
      for (let i = 0; i < frameCount; i++) mono[i] += data[i] / channelCount;
    }

    const combined = new Float32Array(this.inputBuffer.length + mono.length);
    combined.set(this.inputBuffer, 0);
    combined.set(mono, this.inputBuffer.length);
    this.inputBuffer = combined;

    while (this.readPos + this.ratio < this.inputBuffer.length - 1) {
      const idx = Math.floor(this.readPos);
      const frac = this.readPos - idx;
      const s0 = this.inputBuffer[idx];
      const s1 = this.inputBuffer[idx + 1];
      this.outputBuffer.push(s0 + (s1 - s0) * frac);
      this.readPos += this.ratio;
    }

    const consumed = Math.floor(this.readPos);
    if (consumed > 0) {
      this.inputBuffer = this.inputBuffer.slice(consumed);
      this.readPos -= consumed;
    }

    while (this.outputBuffer.length >= this.samplesPerChunk) {
      const chunk = this.outputBuffer.splice(0, this.samplesPerChunk);
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const v = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = v < 0 ? v * 32768 : v * 32767;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-worklet", PCMWorkletProcessor);
