// Fixed-memory PCM ring buffer. AudioWorklet runs on the browser's audio thread.
class RobotPCMPlayer extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(32000)
    this.write = 0
    this.read = 0
    this.count = 0
    this.phase = 0
    this.ready = false
    this.port.onmessage = ({ data }) => {
      if (!(data instanceof Float32Array)) return
      // Bound latency to 400 ms when a background tab or slow device falls behind.
      if (this.count + data.length > 6400) {
        this.count = 0; this.read = this.write; this.ready = false; this.phase = 0
      }
      for (const value of data) {
        this.buffer[this.write] = value
        this.write = (this.write + 1) % this.buffer.length
        this.count++
      }
    }
  }
  process(_inputs, outputs) {
    const output = outputs[0][0]
    if (!this.ready && this.count >= 1600) this.ready = true
    if (!this.ready) return true
    const step = 16000 / sampleRate
    for (let i = 0; i < output.length; i++) {
      if (this.count < 2) { this.ready = false; break }
      const next = this.buffer[(this.read + 1) % this.buffer.length]
      output[i] = this.buffer[this.read] * (1 - this.phase) + next * this.phase
      this.phase += step
      while (this.phase >= 1 && this.count > 0) {
        this.phase--; this.read = (this.read + 1) % this.buffer.length; this.count--
      }
    }
    return true
  }
}
registerProcessor('robot-pcm-player', RobotPCMPlayer)
