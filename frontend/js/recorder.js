// In-browser recording via MediaRecorder, with a live level meter.

const TYPES = [
  ["audio/webm;codecs=opus", "webm"],
  ["audio/webm", "webm"],
  ["audio/mp4", "m4a"],
  ["audio/ogg;codecs=opus", "ogg"],
];

export function recordingSupported() {
  return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
}

export class Recorder {
  constructor({ canvas, onTick }) {
    this.canvas = canvas;
    this.onTick = onTick;
    this.chunks = [];
    this.elapsed = 0;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    const [mime, ext] = TYPES.find(([t]) => MediaRecorder.isTypeSupported(t)) || ["", "webm"];
    this.ext = ext;
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime, audioBitsPerSecond: 96000 } : undefined);
    this.mime = this.rec.mimeType || mime || "audio/webm";
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.rec.start(1000);
    this.startedAt = performance.now();
    this.paused = false;
    this.accum = 0;
    this._meter();
    this._timer = setInterval(() => this.onTick?.(this.time()), 250);
  }

  time() {
    return (this.accum + (this.paused ? 0 : performance.now() - this.startedAt)) / 1000;
  }

  pause() {
    if (!this.rec || this.paused) return;
    this.rec.pause();
    this.accum += performance.now() - this.startedAt;
    this.paused = true;
  }

  resume() {
    if (!this.rec || !this.paused) return;
    this.rec.resume();
    this.startedAt = performance.now();
    this.paused = false;
  }

  stop() {
    return new Promise((resolve) => {
      if (!this.rec || this.rec.state === "inactive") return resolve(null);
      const duration = this.time();
      this.rec.onstop = () => {
        this._teardown();
        resolve({ blob: new Blob(this.chunks, { type: this.mime }), ext: this.ext, duration });
      };
      this.rec.stop();
    });
  }

  cancel() {
    if (this.rec && this.rec.state !== "inactive") {
      this.rec.onstop = null;
      this.rec.stop();
    }
    this._teardown();
  }

  _teardown() {
    clearInterval(this._timer);
    cancelAnimationFrame(this._raf);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    this.ctx = null;
  }

  _meter() {
    if (!this.canvas) return;
    try {
      this.ctx = new AudioContext();
    } catch {
      return;
    }
    const src = this.ctx.createMediaStreamSource(this.stream);
    const analyser = this.ctx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    const c = this.canvas;
    const g = c.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim() || "#ff5b1f";
    const dim = "rgba(239,236,228,0.18)";
    const history = [];
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth;
      const hgt = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = hgt * dpr; }
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
      history.push(this.paused ? 0 : peak);
      const barW = 4 * dpr;
      const gap = 3 * dpr;
      const max = Math.floor(c.width / (barW + gap));
      while (history.length > max) history.shift();
      g.clearRect(0, 0, c.width, c.height);
      history.forEach((v, i) => {
        const bh = Math.max(2 * dpr, Math.min(1, v * 2.2) * c.height);
        g.fillStyle = i === history.length - 1 ? accent : dim;
        g.fillRect(i * (barW + gap), (c.height - bh) / 2, barW, bh);
      });
      this._raf = requestAnimationFrame(draw);
    };
    draw();
  }
}
