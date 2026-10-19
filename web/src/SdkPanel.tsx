import { useEffect, useRef, useState } from "react";
import { errorText, request } from "./api";
import { Icon } from "./Icons";
import type { Receipt, RobotState, SdkCatalog, SdkInterface } from "./types";
import { useSdk } from "./useSdk";

const kindNames = {
  service: "服务调用",
  publish: "话题发布",
  subscribe: "话题订阅",
  transport: "媒体通道",
};
const notes: Record<string, string> = {
  SetServo:
    "形态舵机：position 0–90 度，speed 100–1000 步/秒。专用面板位于「舵机与关节」。",
  SetMcMotion:
    "type 选择厂家模型类型；operation 为 1 预加载、2 执行；motion/model/resource 填写机器人上实际存在的资源。",
  SetMcInputSource:
    "显式添加、修改、删除、启用或禁用控制源。原始速度发布使用这里登记的 source 名称。",
  PlayAudioFile:
    "file.file_path 是机器人文件系统中的路径。播放电脑上的文件可选择「上传音频到扬声器」。",
  PlayEmotion:
    "type=emotion 使用 emotion_ids；type=file 使用 file_paths。视频为机器人上的 MP4 绝对路径。",
  ShutdownTask:
    "发送此请求会让机器人立即进入关机流程，请在实际需要关机时使用。",
  SetNeckLight:
    "enable 控制开关；brightness 为 0–100 的预留参数，厂家当前实现不支持亮度调节。",
  JointCommandArray:
    "HAL 底层关节：须启用厂家低层开发模式、关闭高层 MC，频率 500–1000 Hz。建议使用专用关节面板。",
  McCustomJointCommand:
    "MC 上肢：须进入 BIPED_CUSTOM_UPPER，100 Hz 发布完整上肢目标。建议使用专用关节面板。",
  McLocomotionVelocity:
    "填写已登记的 source，单位 m/s 与 rad/s。本次发布结束时发送零速度。常用短时移动可直接使用工作台方向键。",
  AudioCapture:
    "消息定义取自同一版本厂家文档，原源码未包含此文件；已补充编译。固件是否发布此话题需以收到的数据为准。",
  AudioPlayback:
    "消息定义取自同一版本厂家文档。data.data 为 PCM 字节数组，按 info 设置音频格式，单次消息最大 1 MiB。",
};

function schemaTypes(
  catalog: SdkCatalog,
  name: string,
  found = new Set<string>(),
): Set<string> {
  name = name.replace(/\[\]$/, "");
  if (found.has(name) || !catalog.definitions[name]) return found;
  found.add(name);
  for (const field of catalog.definitions[name].fields)
    schemaTypes(catalog, field.type, found);
  return found;
}

export function SdkPanel({
  state,
  offline,
  notify,
  showOverview,
  showJoints,
}: {
  state: RobotState | null;
  offline: boolean;
  notify: (message: string, error?: boolean) => void;
  showOverview: () => void;
  showJoints: () => void;
}) {
  const sdk = useSdk(state, notify);
  const [selectedId, setSelectedId] = useState("GetMcAction");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部");
  const [editor, setEditor] = useState("{}");
  const [rate, setRate] = useState(1);
  const [duration, setDuration] = useState(1000);
  const [watching, setWatching] = useState(false);
  const [sample, setSample] = useState<unknown>(null);
  const socket = useRef<WebSocket | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [audioBusy, setAudioBusy] = useState(false);
  const [audioReceipt, setAudioReceipt] = useState<Receipt | null>(null);
  const audioId = useRef<string | null>(null);
  const alive = useRef(true);
  const selected = sdk.catalog?.entries.find((item) => item.id === selectedId);
  const locked = !state?.sdk_ready || !!state.read_only || offline;
  const entries = sdk.catalog?.entries ?? [];
  const filtered = entries.filter(
    (item) =>
      (category === "全部" || item.category === category) &&
      `${item.id} ${item.label} ${item.path}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      socket.current?.close();
      if (audioId.current)
        void request("/sdk/audio/stop", { command_id: audioId.current }).catch(
          () => {},
        );
    };
  }, []);
  useEffect(() => {
    if (!selected) return;
    socket.current?.close();
    socket.current = null;
    setWatching(false);
    setSample(null);
    setEditor(JSON.stringify(selected.template, null, 2));
    setRate(selected.rate_hz || 1);
  }, [selected]);

  const execute = () => {
    if (!selected) return;
    try {
      const payload: unknown = JSON.parse(editor);
      if (!payload || Array.isArray(payload) || typeof payload !== "object")
        throw new Error("请求参数须为 JSON 对象");
      void sdk.execute(
        selected.kind === "service" ? "/sdk/call" : "/sdk/publish",
        {
          interface: selected.id,
          payload,
          ...(selected.kind === "publish"
            ? { rate_hz: rate, duration_ms: duration }
            : {}),
        },
      );
    } catch (e) {
      notify(errorText(e), true);
    }
  };
  const watch = () => {
    if (!selected) return;
    if (socket.current) {
      socket.current.close();
      socket.current = null;
      setWatching(false);
      return;
    }
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sdk/topics/${selected.id}/stream`,
    );
    socket.current = ws;
    ws.onopen = () => setWatching(true);
    ws.onmessage = (event) => {
      try {
        const value = JSON.parse(String(event.data));
        setSample(value);
        if (value.type === "error") notify(value.detail, true);
      } catch {
        notify("话题数据格式异常", true);
      }
    };
    ws.onerror = () => notify("话题连接失败，请查看 SDK 状态", true);
    ws.onclose = () => {
      if (socket.current === ws) {
        socket.current = null;
        setWatching(false);
      }
    };
  };
  const upload = async () => {
    if (!file) return;
    const format = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["wav", "mp3", "ogg", "flac", "pcm"].includes(format))
      return notify("请选择 WAV、MP3、OGG、FLAC 或 PCM 文件", true);
    setAudioBusy(true);
    try {
      const id = sdk.identifier();
      const response = await fetch(
        `/api/sdk/audio/play?format=${format}&command_id=${encodeURIComponent(id)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
          signal: AbortSignal.timeout(30000),
        },
      );
      let value = (await response.json()) as Receipt & { detail: string };
      if (!response.ok) throw new Error(value.detail);
      audioId.current = id;
      if (alive.current) setAudioReceipt(value);
      while (alive.current && value.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        value = await request<Receipt>(`/commands/${id}`);
        if (alive.current) setAudioReceipt(value);
      }
      notify(value.detail, ["failed", "unknown"].includes(value.status));
    } catch (e) {
      notify(errorText(e), true);
    } finally {
      audioId.current = null;
      if (alive.current) setAudioBusy(false);
    }
  };

  return (
    <div className="sdk-workspace">
      <div className="sdk-summary">
        <div>
          <span className="eyebrow">T1 / COMPLETE INTERFACES</span>
          <h2>从参数到执行，完整开放。</h2>
          <p>
            18 个服务 · 10 个话题 · 3
            条媒体通道。每次操作由你主动发起，回执保留厂家原始字段。
          </p>
        </div>
        <span className="sdk-count">
          31<small>接口</small>
        </span>
      </div>
      {sdk.error && <div className="connection-alert">{sdk.error}</div>}
      <div className="sdk-browser">
        <aside className="card sdk-directory">
          <input
            aria-label="搜索 SDK 接口"
            className="sdk-search"
            placeholder="搜索名称、功能或路径…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="sdk-categories">
            {["全部", ...new Set(entries.map((item) => item.category))].map(
              (name) => (
                <button
                  className={category === name ? "selected" : ""}
                  key={name}
                  onClick={() => setCategory(name)}
                >
                  {name}
                </button>
              ),
            )}
          </div>
          <div className="sdk-interface-list">
            {filtered.map((item) => (
              <button
                key={item.id}
                className={selectedId === item.id ? "selected" : ""}
                onClick={() => setSelectedId(item.id)}
              >
                <div>
                  <strong>{item.label}</strong>
                  <span>{item.write ? "写入" : "读取"}</span>
                </div>
                <code>{item.id}</code>
              </button>
            ))}
          </div>
        </aside>
        {selected && (
          <section className="card sdk-editor-card">
            <div className="card-heading">
              <h2>{selected.label}</h2>
              <span className="tag green-tag">{kindNames[selected.kind]}</span>
            </div>
            <div className="sdk-endpoint">
              <code>{selected.path}</code>
              <span>{selected.type}</span>
            </div>
            {(notes[selected.id] || selected.note) && (
              <p className="panel-description">
                {notes[selected.id] ?? selected.note}
              </p>
            )}
            {["SetServo", "JointCommandArray", "McCustomJointCommand"].includes(
              selected.id,
            ) && (
              <button className="button subtle" onClick={showJoints}>
                打开舵机与关节面板 <Icon name="arrow" size={14} />
              </button>
            )}
            {(selected.kind === "service" || selected.kind === "publish") && (
              <>
                <div className="editor-toolbar">
                  <label htmlFor="sdk-payload">请求参数 · JSON</label>
                  <button
                    onClick={() =>
                      setEditor(JSON.stringify(selected.template, null, 2))
                    }
                  >
                    恢复参数模板
                  </button>
                </div>
                <textarea
                  id="sdk-payload"
                  className="sdk-json-editor"
                  spellCheck={false}
                  value={editor}
                  onChange={(e) => setEditor(e.target.value)}
                />
                {selected.kind === "publish" && (
                  <div className="sdk-form-grid two-columns">
                    <label>
                      频率 / Hz
                      <input
                        type="number"
                        min="1"
                        max="1000"
                        value={rate}
                        onChange={(e) => setRate(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      持续时间 / ms
                      <input
                        type="number"
                        min="1"
                        max="10000"
                        value={duration}
                        onChange={(e) => setDuration(Number(e.target.value))}
                      />
                    </label>
                  </div>
                )}
                <div className="sdk-dispatch">
                  <button
                    className="button primary"
                    disabled={
                      sdk.busy ||
                      offline ||
                      !state?.sdk_ready ||
                      (selected.write && locked)
                    }
                    onClick={execute}
                  >
                    {sdk.busy
                      ? "处理中…"
                      : selected.id === "ShutdownTask"
                        ? "发送关机请求"
                        : selected.kind === "publish"
                          ? "开始本次发布"
                          : "调用此接口"}
                    <Icon name="arrow" size={15} />
                  </button>
                  {sdk.streamId && (
                    <button className="button" onClick={() => void sdk.stop()}>
                      <Icon name="stop" size={15} />
                      结束本次发布
                    </button>
                  )}
                  <span>时间戳由 SDK 自动填写</span>
                </div>
              </>
            )}
            {selected.kind === "subscribe" && (
              <>
                <div className="sdk-dispatch">
                  <button
                    className="button primary"
                    disabled={offline || !state?.sdk_ready}
                    onClick={watch}
                  >
                    {watching ? "停止监听" : "监听此话题"}
                    <Icon name={watching ? "pause" : "play"} size={15} />
                  </button>
                  <span>
                    {watching
                      ? "实时消息通过 WebSocket 更新"
                      : "点击后开始订阅；没有样本时显示等待"}
                  </span>
                </div>
                <pre className="sdk-response">
                  {sample
                    ? JSON.stringify(sample, null, 2).slice(0, 40000)
                    : "等待订阅消息…"}
                </pre>
              </>
            )}
            {selected.id === "TcpAudioPlayback" && (
              <div className="sdk-upload">
                <Icon name="volume" size={36} />
                <h3>在机器人扬声器播放本地音频</h3>
                <p>WAV / MP3 / OGG / FLAC / PCM · 最多 16 MiB、120 秒</p>
                <input
                  aria-label="选择音频文件"
                  type="file"
                  accept=".wav,.mp3,.ogg,.flac,.pcm"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
                <p className="muted small">
                  转换为 48 kHz / mono / s16le，发送到机器人配置的 6001
                  端口。需要机器人已开启该播放端口。
                </p>
                <div className="inline-buttons">
                  <button
                    className="button primary"
                    disabled={locked || audioBusy || !file}
                    onClick={() => void upload()}
                  >
                    {audioBusy ? "发送中…" : "上传并播放"}
                  </button>
                  <button
                    className="button"
                    disabled={!audioBusy}
                    onClick={() => {
                      if (audioId.current)
                        void request("/sdk/audio/stop", {
                          command_id: audioId.current,
                        }).catch((e) => notify(errorText(e), true));
                    }}
                  >
                    停止发送
                  </button>
                </div>
                {audioReceipt && (
                  <pre>{JSON.stringify(audioReceipt, null, 2)}</pre>
                )}
              </div>
            )}
            {selected.kind === "transport" &&
              selected.id !== "TcpAudioPlayback" && (
                <div className="sdk-transport-link">
                  <Icon
                    name={selected.id === "RTSP" ? "video" : "mic"}
                    size={40}
                  />
                  <p>
                    {selected.id === "RTSP"
                      ? "左右目视频、单帧截图和画面下载已接入工作台。"
                      : "机器人麦克风音频通过 WebSocket 在浏览器播放。"}
                  </p>
                  <button className="button primary" onClick={showOverview}>
                    打开工作台 <Icon name="arrow" size={15} />
                  </button>
                </div>
              )}
            {sdk.catalog && selected.schema && (
              <SchemaReference catalog={sdk.catalog} selected={selected} />
            )}
            {sdk.receipt && (
              <div className="sdk-response-panel">
                <div className="editor-toolbar">
                  <strong>最近执行回执</strong>
                  <span className="tag neutral">{sdk.receipt.status}</span>
                </div>
                <pre className="sdk-response">
                  {JSON.stringify(sdk.receipt, null, 2)}
                </pre>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function SchemaReference({
  catalog,
  selected,
}: {
  catalog: SdkCatalog;
  selected: SdkInterface;
}) {
  const names = [...schemaTypes(catalog, selected.schema!)];
  return (
    <details className="sdk-schema-reference">
      <summary>查看字段类型与厂家枚举</summary>
      {names.map((name) => {
        const definition = catalog.definitions[name];
        return (
          <div key={name}>
            <h4 className="mono">{name}</h4>
            <div className="sdk-table-wrap">
              <table className="sdk-table">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>类型</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {definition.fields.map((field) => (
                    <tr key={field.name}>
                      <td>
                        <code>{field.name}</code>
                      </td>
                      <td>
                        <code>{field.type}</code>
                      </td>
                      <td>{field.description || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {Object.keys(definition.constants).length > 0 && (
              <div className="sdk-enums">
                {Object.entries(definition.constants).map(([key, value]) => (
                  <span key={key}>
                    <code>{key}</code>
                    <b>{value}</b>
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </details>
  );
}
