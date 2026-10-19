import { useEffect, useState } from "react";
import { errorText, request } from "./api";
import { Icon } from "./Icons";
import type { RobotState, TopicSample } from "./types";
import { useSdk } from "./useSdk";

interface JointState {
  name: string;
  position: number | null;
  velocity: number | null;
  effort: number | null;
}
const bodyNames: Record<string, string> = {
  FL: "左上肢",
  FR: "右上肢",
  RL: "左下肢",
  RR: "右下肢",
};

export function JointPanel({
  state,
  offline,
  notify,
}: {
  state: RobotState | null;
  offline: boolean;
  notify: (message: string, error?: boolean) => void;
}) {
  const sdk = useSdk(state, notify);
  const [position, setPosition] = useState(0);
  const [speed, setSpeed] = useState(100);
  const [channel, setChannel] = useState<"hal" | "upper">("hal");
  const [joint, setJoint] = useState("FL_HIP_ROLL_Joint");
  const [target, setTarget] = useState("");
  const [velocity, setVelocity] = useState(0);
  const [effort, setEffort] = useState(0);
  const [stiffness, setStiffness] = useState(20);
  const [damping, setDamping] = useState(2);
  const [duration, setDuration] = useState(1000);
  const [rate, setRate] = useState(500);
  const [sample, setSample] = useState<TopicSample | null>(null);
  const [sampleError, setSampleError] = useState("");
  const locked = !state?.sdk_ready || state.read_only || offline;
  const limits =
    sdk.catalog?.joints.filter((item) => channel === "hal" || item.upper) ?? [];
  const selected = limits.find((item) => item.name === joint);
  const rows =
    (sample?.message as { joints?: JointState[] } | null)?.joints ?? [];
  const fresh = sample?.availability === "fresh";

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const read = async () => {
      if (busy || !state?.sdk_ready) return;
      busy = true;
      try {
        const value = await request<TopicSample>(
          "/sdk/topics/JointStateArray",
          undefined,
          controller.signal,
        );
        setSample(value);
        setSampleError("");
      } catch (e) {
        if (!controller.signal.aborted) setSampleError(errorText(e));
      } finally {
        busy = false;
      }
    };
    void read();
    const timer = window.setInterval(() => void read(), 1000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [state?.sdk_ready]);

  const sendJoint = () => {
    if (!target.trim() || !Number.isFinite(Number(target)))
      return notify("请填写目标位置", true);
    void sdk.execute("/sdk/publish", {
      interface:
        channel === "hal" ? "JointCommandArray" : "McCustomJointCommand",
      payload: {
        joints: [
          {
            name: joint,
            position: Number(target),
            velocity,
            effort,
            stiffness,
            damping,
          },
        ],
      },
      rate_hz: channel === "hal" ? rate : 100,
      duration_ms: duration,
    });
  };
  return (
    <div className="joint-workspace">
      <section className="card servo-card">
        <div className="card-heading">
          <h2>
            <Icon name="move" />
            形态舵机
          </h2>
          <span className="tag green-tag">SetServo</span>
        </div>
        <p className="panel-description">
          控制形态舵机的位置与速度。0° 对应四足形态位置，90° 对应双足形态位置。
        </p>
        <div className="servo-value">
          {position}
          <small>°</small>
        </div>
        <input
          aria-label="舵机目标角度"
          type="range"
          min="0"
          max="90"
          value={position}
          onChange={(e) => setPosition(Number(e.target.value))}
        />
        <div className="range-endpoints">
          <span>0° · 四足</span>
          <span>90° · 双足</span>
        </div>
        <div className="sdk-form-grid two-columns">
          <label>
            目标角度 / °
            <input
              type="number"
              min="0"
              max="90"
              value={position}
              onChange={(e) => setPosition(Number(e.target.value))}
            />
          </label>
          <label>
            速度 / 步每秒
            <input
              type="number"
              min="100"
              max="1000"
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            />
          </label>
        </div>
        <button
          className="button primary wide-button"
          disabled={locked || sdk.busy}
          onClick={() =>
            void sdk.execute("/sdk/call", {
              interface: "SetServo",
              payload: { position, speed },
            })
          }
        >
          发送舵机目标 <Icon name="arrow" size={15} />
        </button>
        <p className="muted small">
          厂家范围：0–90°，100–1000 步/秒；每步 0.225°。
        </p>
      </section>

      <section className="card joint-control-card">
        <div className="card-heading">
          <h2>
            <Icon name="activity" />
            单关节控制
          </h2>
          <span className="tag neutral">14 JOINTS</span>
        </div>
        <div className="segmented joint-channel">
          <button
            className={channel === "hal" ? "selected" : ""}
            onClick={() => {
              setChannel("hal");
              setDamping(2);
              setTarget("");
            }}
          >
            HAL 底层关节
          </button>
          <button
            className={channel === "upper" ? "selected" : ""}
            onClick={() => {
              setChannel("upper");
              setDamping(3);
              setJoint("FL_HIP_ROLL_Joint");
              setTarget("");
            }}
          >
            MC 自定义上肢
          </button>
        </div>
        <p className="panel-description">
          {channel === "hal"
            ? "通过关节指令话题设置位置、速度、力矩及 PD 参数。机器人须处于低层运控开发模式，且高层 MC 已关闭。"
            : "控制上肢 6 个关节。先进入 BIPED_CUSTOM_UPPER 模式，保持原生 MC 运行。"}
        </p>
        {channel === "upper" && (
          <button
            className="button subtle"
            disabled={locked || sdk.busy}
            onClick={() =>
              void sdk.execute("/sdk/call", {
                interface: "SetMcAction",
                payload: {
                  source: "sdk_rpc",
                  command: {
                    action: { value: 1014 },
                    action_desc: "BIPED_CUSTOM_UPPER",
                  },
                },
              })
            }
          >
            进入自定义上肢模式
          </button>
        )}
        <div className="sdk-form-grid">
          <label className="span-two">
            关节
            <select
              value={joint}
              onChange={(e) => {
                setJoint(e.target.value);
                setTarget("");
              }}
            >
              {limits.map((item) => (
                <option key={item.name} value={item.name}>
                  {bodyNames[item.name.slice(0, 2)]} · {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            目标位置 / rad
            <input
              type="number"
              step="0.001"
              min={selected?.minimum}
              max={selected?.maximum}
              placeholder="输入目标角度"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </label>
          <label>
            目标速度 / rad/s
            <input
              type="number"
              step="0.01"
              value={velocity}
              onChange={(e) => setVelocity(Number(e.target.value))}
            />
          </label>
          <label>
            目标力矩 / N·m
            <input
              type="number"
              min="-60"
              max="60"
              step="0.1"
              value={effort}
              onChange={(e) => setEffort(Number(e.target.value))}
            />
          </label>
          <label>
            刚度 Kp
            <input
              type="number"
              min="0"
              step="0.1"
              value={stiffness}
              onChange={(e) => setStiffness(Number(e.target.value))}
            />
          </label>
          <label>
            阻尼 Kd
            <input
              type="number"
              min="0"
              step="0.1"
              value={damping}
              onChange={(e) => setDamping(Number(e.target.value))}
            />
          </label>
          <label>
            持续时间 / ms
            <input
              type="number"
              min="100"
              max="10000"
              step="100"
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
            />
          </label>
          {channel === "hal" && (
            <label>
              发布频率 / Hz
              <input
                type="number"
                min="500"
                max="1000"
                step="100"
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
              />
            </label>
          )}
        </div>
        <div className="joint-range">
          位置范围{" "}
          <strong>
            {selected?.minimum ?? "—"} — {selected?.maximum ?? "—"} rad
          </strong>
          <button
            onClick={() => {
              const current = rows.find((row) => row.name === joint);
              if (fresh && current?.position != null)
                setTarget(String(current.position));
              else notify("尚无新鲜位置反馈", true);
            }}
          >
            填入当前位置
          </button>
        </div>
        <div className="inline-buttons">
          <button
            className="button primary"
            disabled={locked || sdk.busy || !target.trim()}
            onClick={sendJoint}
          >
            发送关节目标
          </button>
          <button
            className="button"
            disabled={!sdk.streamId}
            onClick={() => void sdk.stop()}
          >
            <Icon name="stop" size={15} />
            结束本次发布
          </button>
        </div>
        <p className="panel-footnote">
          其他关节以实时反馈补全，保持当前位置（Kp 20；HAL 的 Kd 2，上肢 Kd
          3）。缺失反馈会返回具体原因。HAL
          结束时发送全关节阻尼帧；页面离开或续租中断也会结束本次发布。
        </p>
        <details className="sdk-help">
          <summary>开发模式与发布频率说明</summary>
          <p>
            HAL：在机器人终端使用厂家 <code>yamo mode edit</code>
            ，选择低层运控开发，并关闭高层运动控制。此页面不会自动修改机器人开发模式。HAL
            建议 500–1000 Hz，MC 上肢为 100
            Hz；后台独立线程发布，桌面系统与网络不提供硬实时保证。回执显示实际包数和最大间隔。
          </p>
        </details>
      </section>

      <section className="card joint-feedback-card">
        <div className="card-heading">
          <h2>关节实时反馈</h2>
          <span className={`tag ${fresh ? "green-tag" : "neutral"}`}>
            {fresh ? "收到数据" : "等待数据"}
          </span>
        </div>
        {(sampleError || !fresh) && (
          <p className="panel-description">
            {sampleError ||
              sample?.detail ||
              "订阅 JointStateArray，收到数据后显示位置、速度与力矩。"}
          </p>
        )}
        <div className="sdk-table-wrap">
          <table className="sdk-table">
            <thead>
              <tr>
                <th>关节</th>
                <th>位置 / rad</th>
                <th>速度 / rad/s</th>
                <th>力矩 / N·m</th>
              </tr>
            </thead>
            <tbody>
              {(sdk.catalog?.joints ?? []).map((item) => {
                const row = rows.find((row) => row.name === item.name);
                return (
                  <tr key={item.name}>
                    <td>
                      <span>{bodyNames[item.name.slice(0, 2)]}</span>
                      <code>{item.name}</code>
                    </td>
                    <td>
                      {fresh && row?.position != null
                        ? row.position.toFixed(4)
                        : "—"}
                    </td>
                    <td>
                      {fresh && row?.velocity != null
                        ? row.velocity.toFixed(3)
                        : "—"}
                    </td>
                    <td>
                      {fresh && row?.effort != null
                        ? row.effort.toFixed(3)
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      {(sdk.error || sdk.receipt) && (
        <section className="card sdk-result-card">
          <div className="card-heading">
            <h2>SDK 执行回执</h2>
            <span className="tag neutral">
              {sdk.receipt?.status ?? "连接信息"}
            </span>
          </div>
          {sdk.error && <p>{sdk.error}</p>}
          <pre>{sdk.receipt ? JSON.stringify(sdk.receipt, null, 2) : ""}</pre>
        </section>
      )}
    </div>
  );
}
