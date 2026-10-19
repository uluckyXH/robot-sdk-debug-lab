import { useEffect, useRef, useState } from "react";
import { errorText, request } from "./api";
import type { Receipt, RobotState, SdkCatalog } from "./types";

export function useSdk(
  state: RobotState | null,
  notify: (message: string, error?: boolean) => void,
) {
  const [catalog, setCatalog] = useState<SdkCatalog | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [streamId, setStreamId] = useState<string | null>(null);
  const ownedStream = useRef<string | null>(null);
  const mounted = useRef(true);
  const lease = useRef<number | null>(null);

  const endLease = () => {
    if (lease.current !== null) clearInterval(lease.current);
    lease.current = null;
  };
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void request<SdkCatalog>("/sdk/catalog", undefined, controller.signal)
      .then(setCatalog)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorText(e));
      });
    const stopOnHidden = () => {
      if (document.hidden && ownedStream.current) {
        endLease();
        void request("/sdk/stream/stop", {
          command_id: ownedStream.current,
        }).catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", stopOnHidden);
    return () => {
      mounted.current = false;
      controller.abort();
      document.removeEventListener("visibilitychange", stopOnHidden);
      endLease();
      if (ownedStream.current)
        void request("/sdk/stream/stop", {
          command_id: ownedStream.current,
        }).catch(() => {});
    };
  }, []);

  const identifier = () => {
    if (!state?.command_epoch) throw new Error("等待 SDK 连接与命令代次");
    return `${state.command_epoch}:${crypto.randomUUID()}`;
  };
  const execute = async (path: string, body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const commandId = identifier();
      let value = await request<Receipt>(path, {
        ...body,
        command_id: commandId,
      });
      if (mounted.current) setReceipt(value);
      notify(value.detail, ["failed", "unknown"].includes(value.status));
      if (path === "/sdk/publish" && value.status === "running") {
        ownedStream.current = commandId;
        if (mounted.current) setStreamId(commandId);
        lease.current = window.setInterval(() => {
          void request("/sdk/stream/heartbeat", {
            command_id: commandId,
          }).catch(endLease);
        }, 500);
        while (mounted.current) {
          await new Promise((resolve) => setTimeout(resolve, 400));
          if (!mounted.current) break;
          value = await request<Receipt>(`/commands/${commandId}`);
          setReceipt(value);
          if (
            value.output_finished ||
            ["failed", "unknown", "stopped"].includes(value.status)
          )
            break;
        }
        endLease();
        ownedStream.current = null;
        if (mounted.current) {
          setStreamId(null);
          notify(value.detail, ["failed", "unknown"].includes(value.status));
        }
      }
    } catch (e) {
      endLease();
      notify(errorText(e), true);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const stop = async () => {
    if (!ownedStream.current) return;
    endLease();
    try {
      setReceipt(
        await request<Receipt>("/sdk/stream/stop", {
          command_id: ownedStream.current,
        }),
      );
    } catch (e) {
      notify(errorText(e), true);
    }
  };
  return { catalog, error, busy, receipt, streamId, execute, stop, identifier };
}
