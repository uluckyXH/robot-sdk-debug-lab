import { useEffect, useRef, useState } from "react";
import { errorText, request, timeLabel } from "./api";
import { ControlPanel } from "./ControlPanel";
import { ConnectionPanel } from "./ConnectionPanel";
import { JointPanel } from "./JointPanel";
import { SdkPanel } from "./SdkPanel";
import { Icon } from "./Icons";
import { getField, Telemetry, valueLabel } from "./Telemetry";
import { useAudio } from "./useAudio";
import { useRobot } from "./useRobot";
import type { LogEvent, RobotState, Snapshot } from "./types";

type Page =
  | "overview"
  | "telemetry"
  | "gallery"
  | "console"
  | "joints"
  | "connection";
const pageNames: Record<Page, string> = {
  overview: "机器人工作台",
  telemetry: "设备遥测",
  gallery: "截图相册",
  console: "SDK 调用台",
  joints: "舵机与关节",
  connection: "连接设置",
};

function SnapshotDialog({
  snapshot,
  close,
}: {
  snapshot: Snapshot;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="snapshot-dialog"
      onClose={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="dialog-heading">
        <div>
          <strong>{snapshot.camera === "left" ? "左目" : "右目"}截图</strong>
          <span>{new Date(snapshot.created_at).toLocaleString("zh-CN")}</span>
        </div>
        <button className="icon-button" aria-label="关闭截图" onClick={close}>
          <Icon name="close" />
        </button>
      </div>
      <img src={snapshot.url} alt="机器人相机截图" />
      <div className="dialog-footer">
        <span className="mono small">
          {snapshot.width} × {snapshot.height} · {snapshot.source.toUpperCase()}{" "}
          · {(snapshot.bytes / 1024).toFixed(0)} KB
        </span>
        <a
          className="button primary"
          href={snapshot.url}
          download={`t1-${snapshot.camera}-${snapshot.id.slice(0, 8)}.jpg`}
        >
          <Icon name="download" size={16} />
          下载原图
        </a>
      </div>
    </dialog>
  );
}

function VideoCard({
  state,
  snapshots,
  refresh,
  notify,
  openSnapshot,
}: {
  state: RobotState | null;
  snapshots: Snapshot[];
  refresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  openSnapshot: (snapshot: Snapshot) => void;
}) {
  const [camera, setCamera] = useState<"left" | "right">("left");
  const [enabled, setEnabled] = useState(true);
  const [version, setVersion] = useState(0);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const video = state?.video[camera];
  const live = enabled && !failed && video?.status === "live";
  const capture = async (source: "sdk" | "video") => {
    setBusy(true);
    try {
      const result = await request<Snapshot>("/snapshots", { camera, source });
      await refresh();
      notify(
        `${source === "sdk" ? "SDK 单帧" : "视频截图"}已保存 · ${result.width} × ${result.height}`,
      );
    } catch (error) {
      notify(errorText(error), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card video-card">
      <div className="card-heading">
        <h2>
          <Icon name="video" />
          实时画面 <span className="tag neutral">VISION</span>
        </h2>
        <div className="segmented" aria-label="选择相机">
          {(["left", "right"] as const).map((value) => (
            <button
              key={value}
              className={camera === value ? "selected" : ""}
              onClick={() => {
                setCamera(value);
                setFailed(false);
              }}
            >
              {value === "left" ? "左目" : "右目"}
            </button>
          ))}
        </div>
      </div>
      <div ref={viewport} className={`video-viewport ${live ? "is-live" : ""}`}>
        {enabled && (
          <img
            key={`${camera}-${version}`}
            className="live-image"
            src={`/api/video/stream?camera=${camera}&v=${version}`}
            alt={`${camera === "left" ? "左" : "右"}目实时画面`}
            onError={() => setFailed(true)}
          />
        )}
        {!live && (
          <div className="video-placeholder">
            <div className="viewfinder">
              <Icon name="video" size={32} />
            </div>
            <strong>
              {!enabled
                ? "预览已暂停"
                : failed || video?.status === "error"
                  ? "视频暂未连接"
                  : "正在连接机器人相机"}
            </strong>
            <span>
              {!enabled
                ? "点击播放继续查看实时画面"
                : (video?.error ?? "等待相机的视频信号")}
            </span>
            {enabled && (failed || video?.status === "error") && (
              <button
                className="video-retry"
                onClick={() => {
                  setFailed(false);
                  setVersion((v) => v + 1);
                }}
              >
                <Icon name="refresh" size={14} />
                重新连接
              </button>
            )}
          </div>
        )}
        <div className="video-topline">
          <span className={`live-label ${live ? "active" : ""}`}>
            <i />
            {live ? "LIVE" : enabled ? "CONNECTING" : "PAUSED"}
          </span>
          <span className="camera-label">
            T1 / {camera === "left" ? "LEFT" : "RIGHT"} CAMERA
          </span>
        </div>
        <span className="corner tl" />
        <span className="corner tr" />
        <span className="corner bl" />
        <span className="corner br" />
        <div className="video-bottomline">
          <span className="mono">
            {live ? `RTSP · ${video?.fps} FPS` : "RTSP"}
            <span className="video-divider">/</span>
            {timeLabel(live ? video?.received_at : null)}
          </span>
          <div>
            <button
              aria-label={enabled ? "暂停视频" : "播放视频"}
              onClick={() => {
                setEnabled((v) => !v);
                setFailed(false);
                setVersion((v) => v + 1);
              }}
            >
              <Icon name={enabled ? "pause" : "play"} size={17} />
            </button>
            <button
              aria-label="全屏视频"
              onClick={() =>
                void viewport.current
                  ?.requestFullscreen()
                  .catch((error) => notify(errorText(error), true))
              }
            >
              <Icon name="expand" size={17} />
            </button>
          </div>
        </div>
      </div>
      <div className="video-actions">
        <div>
          <span className={`status-dot ${live ? "online" : ""}`} />
          <span>{live ? "视频传输中" : enabled ? "等待画面" : "预览暂停"}</span>
          <small>双目视觉 / {camera === "left" ? "左目" : "右目"}</small>
        </div>
        <div className="inline-buttons">
          <button
            className="button subtle"
            disabled={busy || !state?.sdk_ready}
            onClick={() => void capture("sdk")}
          >
            SDK 单帧
          </button>
          <button
            className="button primary"
            disabled={busy || !live}
            onClick={() => void capture("video")}
          >
            <Icon name="camera" size={16} />
            {busy ? "保存中…" : "截取画面"}
          </button>
        </div>
      </div>
      <div className="recent-shots">
        <div className="recent-label">
          <span>最近截图</span>
          <small>{snapshots.length.toString().padStart(2, "0")} CAPTURES</small>
        </div>
        <div className="shot-strip">
          {snapshots.slice(0, 4).map((snapshot) => (
            <button
              key={snapshot.id}
              onClick={() => openSnapshot(snapshot)}
              title={`查看 ${timeLabel(snapshot.created_at)} 的截图`}
            >
              <img
                src={snapshot.url}
                alt={`${timeLabel(snapshot.created_at)} 截图`}
              />
              <span>{timeLabel(snapshot.created_at)}</span>
            </button>
          ))}
          {snapshots.length === 0 && (
            <div className="empty-strip">
              <Icon name="camera" size={19} />
              <span>保存一个瞬间，开始你的调试记录。</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function AudioCard({ configured }: { configured: boolean }) {
  const audio = useAudio();
  const active = audio.status === "live" || audio.status === "connecting";
  return (
    <section
      className={`audio-card ${audio.status === "live" ? "listening" : ""}`}
    >
      <div className="audio-heading">
        <div className="audio-symbol">
          <Icon name="mic" size={20} />
        </div>
        <span className="mono">ROBOT AUDIO</span>
        <span className="audio-state">
          {audio.status === "live"
            ? "正在监听"
            : audio.status === "connecting"
              ? "连接中"
              : "待机"}
        </span>
      </div>
      <h2>
        听见现场<span>。</span>
      </h2>
      <p>机器人麦克风，实时传到你的耳边。</p>
      <div className="waveform" aria-label="机器人音频实时电平">
        {audio.levels.map((level, i) => (
          <i
            key={i}
            style={{
              height: `${4 + level * 52}px`,
              opacity: 0.24 + level * 0.76,
            }}
          />
        ))}
      </div>
      <div className="audio-spec mono">
        <span>
          16 kHz <b>·</b> MONO <b>·</b> PCM
        </span>
        <span>
          {audio.status === "live" ? `${audio.db.toFixed(0)} dBFS` : "— dBFS"}
        </span>
      </div>
      <button
        className="listen-button"
        disabled={!configured}
        onClick={() => (active ? audio.stop() : void audio.start())}
      >
        <Icon name={active ? "pause" : "volume"} size={17} />
        {active ? "结束监听" : "开启声音监听"}
        <Icon name="arrow" size={15} />
      </button>
      {audio.status === "live" && (
        <label className="listen-gain">
          <Icon name="volume" size={15} />
          <input
            aria-label="浏览器监听音量"
            type="range"
            min="0"
            max="3"
            step="0.1"
            value={audio.gain}
            onChange={(e) => audio.setGain(Number(e.target.value))}
          />
          <span>{Math.round(audio.gain * 100)}%</span>
        </label>
      )}
      {(audio.error || !configured) && (
        <p className="audio-error">
          {audio.error || "尚未配置机器人音源，请检查本地 audio 设置。"}
        </p>
      )}
      <div className="audio-footnote">
        <span className="status-dot" />
        {active
          ? "仅在当前页面播放 · 切到后台自动结束"
          : "点击后播放机器人声音"}
      </div>
    </section>
  );
}

function DeviceCard({
  state,
  showDetails,
  offline,
}: {
  state: RobotState | null;
  showDetails: () => void;
  offline: boolean;
}) {
  const field = (group: string, key: string) =>
    offline ? "—" : valueLabel(getField(state, group, key));
  return (
    <section className="card device-card">
      <div className="card-heading">
        <h2>
          <Icon name="robot" />
          设备概览
        </h2>
        <span className="mono small muted">T1</span>
      </div>
      <div className="device-identity">
        <div className="robot-avatar">
          <Icon name="robot" size={31} />
        </div>
        <div>
          <strong>{state?.name ?? "T1 · 开发机器人"}</strong>
          <span>
            <i
              className={`status-dot ${state?.connected && !offline ? "online" : ""}`}
            />
            {state?.connected && !offline ? "机器人已连接" : "等待机器人连接"}
          </span>
        </div>
      </div>
      <dl className="device-properties">
        <div>
          <dt>网络地址</dt>
          <dd className="mono">{state?.robot_ip ?? "—"}</dd>
        </div>
        <div>
          <dt>ROS Domain</dt>
          <dd className="mono">{state?.ros_domain_id ?? "—"}</dd>
        </div>
        <div>
          <dt>当前模式</dt>
          <dd title={field("motion", "mc_action_label")}>
            {field("motion", "mc_action_label")}
          </dd>
        </div>
        <div>
          <dt>控制来源</dt>
          <dd>{field("interaction", "control_input_source")}</dd>
        </div>
        <div>
          <dt>扬声器音量</dt>
          <dd>
            {field("audio", "volume")}
            {field("audio", "volume") !== "—" && "%"}
          </dd>
        </div>
      </dl>
      <button className="card-link" onClick={showDetails}>
        查看完整设备信息
        <Icon name="arrow" size={15} />
      </button>
    </section>
  );
}

function EventList({
  events,
  compact = false,
}: {
  events: LogEvent[];
  compact?: boolean;
}) {
  return (
    <section className="card events-card">
      <div className="card-heading">
        <h2>
          <Icon name="terminal" />
          调用记录
        </h2>
        <span className="tag neutral mono">{events.length} EVENTS</span>
      </div>
      <div className="event-list">
        {events.slice(0, compact ? 5 : 100).map((event) => (
          <div className="event-row" key={event.id}>
            <span
              className={`event-icon ${["error", "failed", "unknown", "blocked"].includes(event.status) ? "event-error" : ""}`}
            >
              <Icon
                name={
                  ["error", "failed", "unknown", "blocked"].includes(
                    event.status,
                  )
                    ? "info"
                    : "check"
                }
                size={13}
              />
            </span>
            <div className="event-description">
              <strong>
                {event.operation}
                <span className="event-status">{event.status}</span>
              </strong>
              <p title={event.detail}>{event.detail}</p>
            </div>
            <div className="event-time mono">
              {timeLabel(event.time)}
              {event.elapsed_ms != null && <small>{event.elapsed_ms} ms</small>}
            </div>
          </div>
        ))}
        {events.length === 0 && (
          <p className="muted empty-events">
            设备连接、截图与 SDK 调用会记录在这里。
          </p>
        )}
      </div>
    </section>
  );
}

export default function App() {
  const robot = useRobot();
  const [page, setPage] = useState<Page>("overview");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [toast, setToast] = useState<{
    message: string;
    error: boolean;
  } | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [filter, setFilter] = useState("all");
  const toastTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
    },
    [],
  );
  const notify = (message: string, error = false) => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    setToast({ message, error });
    toastTimer.current = window.setTimeout(() => setToast(null), 5500);
  };
  const connected = robot.state?.connected && !robot.error;
  const battery = getField(
    robot.state,
    "battery",
    "remaining_capacity_percentage",
  );
  const liveFeeds = Object.values(robot.state?.video ?? {}).filter(
    (stream) => stream.status === "live",
  ).length;
  const stats = [
    {
      icon: "wifi",
      label: "连接状态",
      value: connected ? "已连接" : "未连接",
      suffix: "",
      note: connected
        ? "ROS 2 · 数据已确认"
        : robot.error
          ? "后端连接中断"
          : "等待真实设备反馈",
      good: connected,
    },
    {
      icon: "battery",
      label: "机器人电量",
      value: robot.error ? "—" : valueLabel(battery),
      suffix: battery?.availability === "fresh" && !robot.error ? "%" : "",
      note:
        battery?.availability === "fresh"
          ? "SDK 原始电量读数"
          : "暂无新鲜电池数据",
    },
    {
      icon: "video",
      label: "视频通道",
      value: String(robot.error ? 0 : liveFeeds).padStart(2, "0"),
      suffix: "/ 02",
      note: "双目相机 · 独立切换",
    },
    {
      icon: "camera",
      label: "已保存截图",
      value: String(robot.snapshots.length).padStart(2, "0"),
      suffix: "张",
      note: "本地保存 · 随时下载",
    },
  ];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand-mark"
          aria-label="T1 Studio 首页"
          onClick={() => setPage("overview")}
        >
          T<span>1</span>
        </button>
        <div className="sidebar-line" />
        <nav aria-label="主导航">
          {(
            [
              ["overview", "grid", "工作台"],
              ["telemetry", "activity", "设备信息"],
              ["joints", "move", "舵机与关节"],
              ["gallery", "camera", "截图相册"],
              ["console", "terminal", "SDK 调用台"],
              ["connection", "wifi", "连接设置"],
            ] as const
          ).map(([id, icon, label]) => (
            <button
              key={id}
              aria-label={label}
              title={label}
              className={page === id ? "active" : ""}
              onClick={() => setPage(id)}
            >
              <Icon name={icon} size={21} />
              <span>
                {label === "舵机与关节"
                  ? "关节"
                  : label === "设备信息"
                    ? "设备"
                    : label === "截图相册"
                      ? "相册"
                      : label === "SDK 调用台"
                        ? "调试"
                        : label === "连接设置"
                          ? "连接"
                          : label}
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            title="API 文档"
            aria-label="API 文档"
          >
            <Icon name="book" />
          </a>
          <span className="sidebar-avatar">SDK</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="wordmark">
            T1 <span>Studio</span>
            <i /> <small>ROBOT SDK LAB</small>
          </div>
          <div className="topbar-right">
            <span
              className={`mode-badge ${robot.state?.read_only === false ? "control-enabled" : ""}`}
            >
              <Icon
                name={robot.state?.read_only === false ? "unlock" : "lock"}
                size={13}
              />
              {robot.state?.read_only === false ? "控制模式" : "只读调试模式"}
            </span>
            <span className="topbar-divider" />
            <a href="/docs" target="_blank" rel="noreferrer">
              开发文档 <Icon name="arrow" size={14} />
            </a>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                WORKSPACE <span>/</span> DEVICE 01
              </p>
              <h1>
                {pageNames[page]}
                <span className="heading-dot">.</span>
              </h1>
              <p className="page-description">
                {page === "overview"
                  ? "连接感知与行动，让每一次调试清晰可见。"
                  : page === "telemetry"
                    ? "直接读取 SDK，查看每一项数据的真实状态。"
                    : page === "joints"
                      ? "独立控制形态舵机与关节，查看每个目标和实时反馈。"
                      : page === "gallery"
                        ? "留住机器人的视角，回看每一个调试瞬间。"
                        : page === "connection"
                          ? "在热点与局域网之间切换，让所有连接跟随机器人地址。"
                          : "从接口到回执，追踪每一次设备交互。"}
              </p>
            </div>
            <div className="page-actions">
              <button
                className="device-selector"
                title="修改机器人 IP 地址"
                onClick={() => setPage("connection")}
              >
                <Icon name="robot" size={19} />
                <span>T1 Robot</span>
                <span className={`status-dot ${connected ? "online" : ""}`} />
              </button>
              <button
                className="icon-button refresh-button"
                aria-label="刷新设备状态"
                title="刷新设备状态"
                onClick={() => {
                  void robot.refresh();
                  notify("正在刷新设备状态");
                }}
              >
                <Icon name="refresh" size={18} />
              </button>
            </div>
          </div>
          {robot.error && (
            <div className="connection-alert" role="alert">
              <Icon name="info" size={18} />
              <span>后端连接中断，正在自动重试。已有数据已暂停更新。</span>
              <code>{robot.error}</code>
            </div>
          )}
          {!robot.error && robot.state?.error && (
            <div className="connection-alert" role="alert">
              <Icon name="info" size={18} />
              <span>原生 SDK 尚未就绪：{robot.state.error}</span>
            </div>
          )}
          {page === "overview" && (
            <>
              <div className="stats-grid">
                {stats.map((stat) => (
                  <div className="stat-card" key={stat.label}>
                    <div className="stat-heading">
                      <span>{stat.label}</span>
                      <Icon name={stat.icon} size={18} />
                    </div>
                    <div className={`stat-value ${stat.good ? "green" : ""}`}>
                      {stat.value}
                      <small>{stat.suffix}</small>
                      {stat.good && <span className="connected-pulse" />}
                    </div>
                    <p>{stat.note}</p>
                  </div>
                ))}
              </div>
              <div className="dashboard-grid">
                <div className="main-column">
                  <VideoCard
                    state={robot.error ? null : robot.state}
                    snapshots={robot.snapshots}
                    refresh={robot.refresh}
                    notify={notify}
                    openSnapshot={setSnapshot}
                  />
                  <ControlPanel
                    state={robot.state}
                    capabilities={robot.capabilities}
                    offline={!!robot.error}
                    notify={notify}
                    refresh={robot.refresh}
                  />
                </div>
                <div className="side-column">
                  <DeviceCard
                    state={robot.state}
                    offline={!!robot.error}
                    showDetails={() => setPage("telemetry")}
                  />
                  <AudioCard
                    configured={!!robot.state?.audio.configured && !robot.error}
                  />
                  <EventList events={robot.events} compact />
                </div>
              </div>
            </>
          )}
          {page === "telemetry" && (
            <>
              <div className="section-toolbar">
                <span>
                  <span className={`status-dot ${connected ? "online" : ""}`} />
                  每 2 秒刷新 · 缺失或过期数据单独标记
                </span>
                <button
                  className="button"
                  onClick={() => setShowJson((v) => !v)}
                >
                  <Icon name="code" size={16} />
                  {showJson ? "收起原始数据" : "查看原始 JSON"}
                </button>
              </div>
              {showJson && (
                <pre className="json-view">
                  {JSON.stringify(robot.state, null, 2)}
                </pre>
              )}
              <Telemetry state={robot.state} stale={!!robot.error} />
            </>
          )}
          {page === "gallery" && (
            <>
              <div className="section-toolbar">
                <span>
                  已保存 {robot.snapshots.length} 张 · 自动保留最近 60 张
                </span>
                <div className="segmented">
                  {[
                    ["all", "全部相机"],
                    ["left", "左目"],
                    ["right", "右目"],
                  ].map(([value, label]) => (
                    <button
                      key={value}
                      className={filter === value ? "selected" : ""}
                      onClick={() => setFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="gallery-grid">
                {robot.snapshots
                  .filter((item) => filter === "all" || item.camera === filter)
                  .map((item) => (
                    <button
                      className="card gallery-item"
                      key={item.id}
                      onClick={() => setSnapshot(item)}
                    >
                      <img
                        src={item.url}
                        alt={`${item.camera} ${timeLabel(item.created_at)}`}
                      />
                      <div>
                        <strong>
                          {item.camera === "left" ? "左目相机" : "右目相机"}
                          <span>{item.source.toUpperCase()}</span>
                        </strong>
                        <p>
                          {timeLabel(item.created_at)}{" "}
                          <span>
                            {item.width} × {item.height}
                          </span>
                        </p>
                      </div>
                    </button>
                  ))}
              </div>
              {!robot.snapshots.filter(
                (item) => filter === "all" || item.camera === filter,
              ).length && (
                <div className="card empty-state">
                  <Icon name="camera" size={40} />
                  <h3>这里还没有截图</h3>
                  <p>在工作台截取视频画面，或通过 SDK 获取单帧。</p>
                  <button
                    className="button primary"
                    onClick={() => setPage("overview")}
                  >
                    回到实时画面
                    <Icon name="arrow" size={15} />
                  </button>
                </div>
              )}
            </>
          )}
          {page === "joints" && (
            <JointPanel
              state={robot.state}
              offline={!!robot.error}
              notify={notify}
            />
          )}
          {page === "connection" && (
            <ConnectionPanel
              state={robot.state}
              offline={!!robot.error}
              notify={notify}
            />
          )}
          {page === "console" && (
            <>
              <SdkPanel
                state={robot.state}
                offline={!!robot.error}
                notify={notify}
                showOverview={() => setPage("overview")}
                showJoints={() => setPage("joints")}
              />
              <EventList events={robot.events} />
            </>
          )}
          <footer>
            <span>
              <i
                className={`status-dot ${!robot.error && robot.state ? "online" : ""}`}
              />
              {!robot.error && robot.state
                ? "本地服务运行中"
                : "正在连接本地服务"}
              <span className="footer-dot">·</span>T1 SDK{" "}
              {robot.state?.sdk_commit.slice(0, 7) ?? "—"}
            </span>
            <span>
              T1 STUDIO <b>/</b> BUILT FOR EXPLORATION
            </span>
          </footer>
        </main>
      </div>
      {toast && (
        <div
          role="status"
          className={`toast ${toast.error ? "toast-error" : ""}`}
        >
          <Icon name={toast.error ? "info" : "check"} size={18} />
          <span>{toast.message}</span>
          <button aria-label="关闭提示" onClick={() => setToast(null)}>
            <Icon name="close" size={15} />
          </button>
        </div>
      )}
      {snapshot && (
        <SnapshotDialog snapshot={snapshot} close={() => setSnapshot(null)} />
      )}
    </div>
  );
}
