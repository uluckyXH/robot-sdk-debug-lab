import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, request } from "./api";
import type { Capabilities, LogEvent, RobotState, Snapshot } from "./types";

export function useRobot() {
  const [state, setState] = useState<RobotState | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [error, setError] = useState("");
  const busy = useRef(false);
  const active = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    controller.current = new AbortController();
    const timeout = window.setTimeout(() => controller.current?.abort(), 6000);
    try {
      const [robot, caps, images, logs] = await Promise.all([
        request<RobotState>("/state", undefined, controller.current.signal),
        request<Capabilities>(
          "/capabilities",
          undefined,
          controller.current.signal,
        ),
        request<Snapshot[]>("/snapshots", undefined, controller.current.signal),
        request<LogEvent[]>("/events", undefined, controller.current.signal),
      ]);
      if (!active.current) return;
      setState(robot);
      setCapabilities(caps);
      setSnapshots(images);
      setEvents(logs);
      setError("");
    } catch (err) {
      if (active.current) setError(errorText(err));
    } finally {
      window.clearTimeout(timeout);
      busy.current = false;
    }
  }, []);
  useEffect(() => {
    active.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => {
      active.current = false;
      window.clearInterval(timer);
      controller.current?.abort();
    };
  }, [refresh]);
  return { state, capabilities, snapshots, events, error, refresh };
}
