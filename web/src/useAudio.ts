import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "./api";

export function useAudio() {
  const [status, setStatus] = useState<
    "idle" | "connecting" | "live" | "error"
  >("idle");
  const [error, setError] = useState("");
  const [levels, setLevels] = useState<number[]>(Array(46).fill(0));
  const [gain, setGain] = useState(1);
  const [db, setDb] = useState(-96);
  const resource = useRef<{
    context: AudioContext;
    socket?: WebSocket;
    node?: AudioWorkletNode;
    gain?: GainNode;
    timer?: number;
  } | null>(null);
  const generation = useRef(0);

  const stop = useCallback(() => {
    generation.current++;
    const item = resource.current;
    resource.current = null;
    if (item) {
      window.clearInterval(item.timer);
      item.socket?.close();
      item.node?.disconnect();
      item.gain?.disconnect();
      void item.context.close().catch(() => undefined);
    }
    setStatus("idle");
    setLevels(Array(46).fill(0));
    setDb(-96);
  }, []);

  const start = useCallback(async () => {
    stop();
    setStatus("connecting");
    setError("");
    const version = generation.current;
    try {
      const context = new AudioContext();
      const item: NonNullable<typeof resource.current> = { context };
      resource.current = item;
      await context.resume();
      await context.audioWorklet.addModule("/pcm-player.js");
      if (version !== generation.current) return;
      item.node = new AudioWorkletNode(context, "robot-pcm-player");
      item.gain = context.createGain();
      item.gain.gain.value = gain;
      item.node.connect(item.gain).connect(context.destination);
      const socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/audio`,
      );
      socket.binaryType = "arraybuffer";
      item.socket = socket;
      let lastMeter = 0;
      socket.onopen = () => {
        item.timer = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send("ping");
        }, 2500);
      };
      socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
        if (version !== generation.current) return;
        if (typeof event.data === "string") {
          const message = JSON.parse(event.data) as {
            type: string;
            detail?: string;
          };
          if (message.type === "error") {
            stop();
            setStatus("error");
            setError(message.detail ?? "音频连接失败");
          }
          return;
        }
        const pcm = new Int16Array(event.data);
        const samples = new Float32Array(pcm.length);
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) {
          samples[i] = pcm[i] / 32768;
          sum += samples[i] ** 2;
        }
        item.node?.port.postMessage(samples, [samples.buffer]);
        setStatus("live");
        if (performance.now() - lastMeter > 80) {
          const decibels = Math.max(
            -96,
            20 *
              Math.log10(Math.sqrt(sum / Math.max(1, pcm.length)) || 0.000015),
          );
          setDb(decibels);
          setLevels((previous) => [
            ...previous.slice(1),
            Math.max(0, (decibels + 70) / 70),
          ]);
          lastMeter = performance.now();
        }
      };
      const disconnected = () => {
        if (version !== generation.current) return;
        stop();
        setStatus("error");
        setError("机器人音频连接已断开，请重新开启监听");
      };
      socket.onerror = disconnected;
      socket.onclose = disconnected;
    } catch (err) {
      if (version !== generation.current) return;
      stop();
      setError(errorText(err));
      setStatus("error");
    }
  }, [gain, stop]);

  useEffect(() => {
    if (resource.current?.gain) resource.current.gain.gain.value = gain;
  }, [gain]);
  useEffect(() => {
    const hide = () => {
      if (document.hidden) stop();
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      stop();
    };
  }, [stop]);
  return { status, error, levels, gain, setGain, db, start, stop };
}
