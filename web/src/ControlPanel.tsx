import { useEffect, useRef, useState } from "react";
import { errorText, request } from "./api";
import { Icon } from "./Icons";
import type { Capabilities, Receipt, RobotState } from "./types";

export function ControlPanel({
  state,
  capabilities,
  offline,
  notify,
  refresh,
}: {
  state: RobotState | null;
  capabilities: Capabilities | null;
  offline: boolean;
  notify: (message: string, error?: boolean) => void;
  refresh: () => Promise<void>;
}) {
  const locked = !state || state.read_only || offline || !state.sdk_ready;
  const [tab, setTab] = useState<"motion" | "output">("motion");
  const [preset, setPreset] = useState("wave_right");
  const [text, setText] = useState("你好，很高兴见到你。");
  const [volume, setVolume] = useState(20);
  const [emotion, setEmotion] = useState(10);
  const [pending, setPending] = useState(false);
  const [last, setLast] = useState<Receipt | null>(null);
  const heartbeat = useRef<number | null>(null);
  const mounted = useRef(true);
  const cleanup = () => {
    if (heartbeat.current !== null) window.clearInterval(heartbeat.current);
    heartbeat.current = null;
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cleanup();
    };
  }, []);

  const run = async (path: string, body: Record<string, unknown>) => {
    if (locked || !state) return;
    const commandId = `${state.command_epoch}:${crypto.randomUUID()}`;
    setPending(true);
    try {
      let result = await request<Receipt>(path, {
        ...body,
        command_id: commandId,
      });
      setLast(result);
      notify(
        result.detail,
        result.status === "failed" || result.status === "unknown",
      );
      if (path === "/motion" && result.status === "accepted") {
        cleanup();
        heartbeat.current = window.setInterval(() => {
          void request("/motion/heartbeat", { command_id: commandId }).catch(
            cleanup,
          );
        }, 500);
        const deadline = Date.now() + 35000;
        while (mounted.current && Date.now() < deadline) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          if (!mounted.current) break;
          result = await request<Receipt>(`/commands/${commandId}`);
          setLast(result);
          if (
            result.output_finished ||
            ["failed", "unknown", "completed", "stopped"].includes(
              result.status,
            )
          )
            break;
        }
        cleanup();
        if (mounted.current)
          notify(
            result.detail,
            result.status === "failed" || result.status === "unknown",
          );
      }
    } catch (err) {
      cleanup();
      if (mounted.current) notify(errorText(err), true);
    } finally {
      if (mounted.current) {
        setPending(false);
        void refresh();
      }
    }
  };
  const move = (action: string) => void run("/motion", { action });
  const actionDisabled = (action: string) =>
    locked ||
    pending ||
    !capabilities?.motion.actions.find((a) => a.action === action)?.available;
  const commandDisabled = (kind: string) =>
    locked ||
    pending ||
    !capabilities?.interfaces.find((a) => a.name === kind)?.available;
  return (
    <section className="card controls">
      <div className="card-heading">
        <h2>
          <Icon name="move" />
          机器人控制
        </h2>
        <span className={`tag ${locked ? "neutral" : "green-tag"}`}>
          <Icon name={locked ? "lock" : "unlock"} size={12} />
          {locked ? "只读保护" : "控制已启用"}
        </span>
      </div>
      <div className="control-tabs">
        <button
          className={tab === "motion" ? "selected" : ""}
          onClick={() => setTab("motion")}
        >
          运动与姿态
        </button>
        <button
          className={tab === "output" ? "selected" : ""}
          onClick={() => setTab("output")}
        >
          语音与表情
        </button>
        <span>SDK CONTROL</span>
      </div>
      {locked && (
        <div className="lock-note">
          <Icon name="lock" size={15} />
          <span>
            {state?.read_only
              ? "当前为只读模式，控制指令已在 SDK 层锁定。"
              : "等待 SDK 连接后才能操作。"}
          </span>
        </div>
      )}
      {tab === "motion" ? (
        <div className="motion-layout">
          <div className="direction-area">
            <div className="dpad">
              <button
                className="direction up"
                aria-label="前进"
                disabled={actionDisabled("walk_forward_short")}
                onClick={() => move("walk_forward_short")}
              >
                <Icon name="up" />
              </button>
              <button
                className="direction left"
                aria-label="左移"
                disabled={actionDisabled("move_left_short")}
                onClick={() => move("move_left_short")}
              >
                <Icon name="left" />
              </button>
              <button
                className="direction stop"
                aria-label="停止本次运动"
                disabled={locked || !capabilities?.motion.stop.available}
                onClick={() => {
                  cleanup();
                  void run("/motion/stop", {});
                }}
              >
                <Icon name="stop" size={17} />
              </button>
              <button
                className="direction right"
                aria-label="右移"
                disabled={actionDisabled("move_right_short")}
                onClick={() => move("move_right_short")}
              >
                <Icon name="right" />
              </button>
              <button
                className="direction down"
                aria-label="后退"
                disabled={actionDisabled("walk_backward_short")}
                onClick={() => move("walk_backward_short")}
              >
                <Icon name="down" />
              </button>
            </div>
            <div className="turn-buttons">
              <button
                disabled={actionDisabled("turn_left_short")}
                onClick={() => move("turn_left_short")}
              >
                ↶ 左转
              </button>
              <button
                disabled={actionDisabled("turn_right_short")}
                onClick={() => move("turn_right_short")}
              >
                右转 ↷
              </button>
            </div>
            <small>短时移动 · 默认 0.8 秒</small>
          </div>
          <div className="postures">
            <label>常用姿态</label>
            <div className="posture-buttons">
              {["stand_biped", "stand_quadruped", "sit_down", "lie_down"].map(
                (name) => (
                  <button
                    key={name}
                    disabled={actionDisabled(name)}
                    onClick={() => move(name)}
                  >
                    {capabilities?.motion.actions.find((a) => a.action === name)
                      ?.label ??
                      (
                        {
                          stand_biped: "双足站立",
                          stand_quadruped: "四足站立",
                          sit_down: "坐下",
                          lie_down: "趴下",
                        } as Record<string, string>
                      )[name]}
                  </button>
                ),
              )}
            </div>
            <label htmlFor="preset">预设动作</label>
            <div className="input-action">
              <select
                id="preset"
                value={preset}
                onChange={(e) => setPreset(e.target.value)}
              >
                {capabilities?.motion.actions
                  .filter((a) => a.kind === "preset")
                  .map((a) => (
                    <option key={a.action} value={a.action}>
                      {a.label}
                    </option>
                  ))}
              </select>
              <button
                disabled={actionDisabled(preset)}
                onClick={() => move(preset)}
              >
                执行 <Icon name="arrow" size={14} />
              </button>
            </div>
            <p className="muted small">动作状态以机器人 SDK 回执为准。</p>
          </div>
        </div>
      ) : (
        <div className="output-controls">
          <label htmlFor="tts">机器人播报</label>
          <div className="input-action">
            <input
              id="tts"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={2000}
            />
            <button
              disabled={commandDisabled("speak") || !text.trim()}
              onClick={() =>
                void run("/commands", { kind: "speak", arguments: { text } })
              }
            >
              发送播报
            </button>
          </div>
          <div className="output-grid">
            <div>
              <label htmlFor="robot-volume">机器人音量 · {volume}%</label>
              <div className="input-action">
                <input
                  id="robot-volume"
                  type="range"
                  min="0"
                  max="100"
                  value={volume}
                  onChange={(e) => setVolume(Number(e.target.value))}
                />
                <button
                  disabled={commandDisabled("set_volume")}
                  onClick={() =>
                    void run("/commands", {
                      kind: "set_volume",
                      arguments: { volume },
                    })
                  }
                >
                  设置
                </button>
              </div>
              <div className="inline-buttons">
                <button
                  disabled={commandDisabled("set_mute")}
                  onClick={() =>
                    void run("/commands", {
                      kind: "set_mute",
                      arguments: { muted: true },
                    })
                  }
                >
                  静音
                </button>
                <button
                  disabled={commandDisabled("set_mute")}
                  onClick={() =>
                    void run("/commands", {
                      kind: "set_mute",
                      arguments: { muted: false },
                    })
                  }
                >
                  取消静音
                </button>
              </div>
            </div>
            <div>
              <label htmlFor="emotion">屏幕表情</label>
              <div className="input-action">
                <select
                  id="emotion"
                  value={emotion}
                  onChange={(e) => setEmotion(Number(e.target.value))}
                >
                  {[
                    ...Array.from({ length: 14 }, (_, i) => i + 10),
                    60,
                    90,
                    102,
                    130,
                    230,
                    240,
                    250,
                    260,
                    270,
                  ].map((id) => (
                    <option key={id} value={id}>
                      表情 {id}
                    </option>
                  ))}
                </select>
                <button
                  disabled={commandDisabled("expression")}
                  onClick={() =>
                    void run("/commands", {
                      kind: "expression",
                      arguments: { emotion_id: emotion },
                    })
                  }
                >
                  显示
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {last && (
        <details className="receipt">
          <summary>最近回执 · {last.status}</summary>
          <pre>{JSON.stringify(last, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
