import { useCallback, useEffect, useState } from "react";
import { errorText, request } from "./api";
import { Icon } from "./Icons";
import type { RobotState } from "./types";

interface Connection {
  robot_ip: string;
  ros_domain_id: number;
  active_robot_ip: string;
  active_ros_domain_id: number;
  pending_restart: boolean;
  automatic_restart: boolean;
  revision: string;
  independent_media: string[];
}

export function ConnectionPanel({
  state,
  offline,
  notify,
}: {
  state: RobotState | null;
  offline: boolean;
  notify: (message: string, error?: boolean) => void;
}) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [address, setAddress] = useState("");
  const [domain, setDomain] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const value = await request<Connection>("/connection", undefined, signal);
      if (signal?.aborted) return;
      setConnection(value);
      setAddress(value.robot_ip);
      setDomain(String(value.ros_domain_id));
      setError("");
    } catch (e) {
      if (!signal?.aborted) setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, state?.command_epoch]);

  const save = async () => {
    if (!connection) return;
    setBusy(true);
    setError("");
    try {
      const value = await request<Connection>("/connection", {
        robot_ip: address.trim(),
        ros_domain_id: Number(domain),
        revision: connection.revision,
      });
      setConnection(value);
      setAddress(value.robot_ip);
      notify(
        value.pending_restart
          ? value.automatic_restart
            ? "已保存，正在按新地址重新连接"
            : "已保存，请重启本地服务使新地址生效"
          : "连接配置已保存",
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="connection-workspace">
      <section className="card connection-form-card">
        <div className="card-heading">
          <h2>
            <Icon name="wifi" />
            机器人连接
          </h2>
          <span className="tag green-tag">NETWORK</span>
        </div>
        <p className="panel-description">
          电脑连接机器人热点或同一局域网后，填写机器人当前的 IP
          地址。地址会保存在本机，下次启动继续使用。
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <label className="connection-address">
            机器人 IPv4 地址
            <input
              required
              autoComplete="off"
              spellCheck={false}
              inputMode="decimal"
              placeholder="例如 192.168.4.1"
              pattern="[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"
              maxLength={15}
              value={address}
              onChange={(event) => setAddress(event.target.value)}
            />
          </label>
          <details className="sdk-help connection-advanced">
            <summary>高级连接参数</summary>
            <label>
              ROS domain
              <input
                type="number"
                min="0"
                max="232"
                step="1"
                required
                value={domain}
                onChange={(event) => setDomain(event.target.value)}
              />
            </label>
            <p>与机器人配置一致；仅切换网络时通常保持原值。</p>
          </details>
          {error && (
            <p className="connection-form-error" role="alert">
              {error}
            </p>
          )}
          <div className="inline-buttons">
            <button
              className="button primary"
              type="submit"
              disabled={busy || offline || !connection}
            >
              <Icon name="refresh" size={16} />
              {busy
                ? "正在保存…"
                : connection?.automatic_restart
                  ? "保存并重新连接"
                  : "保存连接设置"}
            </button>
            <button
              className="button"
              type="button"
              disabled={busy || offline}
              onClick={() => void load()}
            >
              重新读取
            </button>
          </div>
          <p className="panel-footnote">
            {connection?.automatic_restart
              ? "保存后本地服务会短暂重启，页面自动恢复连接。请先结束正在执行的动作或发布任务。"
              : "通过 ./start.sh 启动可自动重连；其他启动方式保存后需手动重启本地服务。"}
          </p>
        </form>
      </section>

      <section className="card connection-status-card">
        <div className="card-heading">
          <h2>当前连接</h2>
          <span
            className={`tag ${state?.connected && !offline ? "green-tag" : "neutral"}`}
          >
            {offline
              ? "服务重连中"
              : connection?.pending_restart
                ? "等待应用新地址"
                : state?.connected
                  ? "收到设备数据"
                  : "等待设备数据"}
          </span>
        </div>
        <dl className="connection-values">
          <div>
            <dt>正在使用</dt>
            <dd>{state?.robot_ip ?? connection?.active_robot_ip ?? "—"}</dd>
          </div>
          <div>
            <dt>已保存地址</dt>
            <dd>{connection?.robot_ip ?? "—"}</dd>
          </div>
          <div>
            <dt>ROS domain</dt>
            <dd>{state?.ros_domain_id ?? "—"}</dd>
          </div>
          <div>
            <dt>页面控制</dt>
            <dd>{state ? (state.read_only ? "只读模式" : "已开启") : "—"}</dd>
          </div>
        </dl>
        <div className="connection-channels">
          <p>
            <Icon name="check" size={16} /> SDK 发现与设备信息
          </p>
          <p>
            <Icon name="check" size={16} /> 左右目视频与截图
          </p>
          <p>
            <Icon name="check" size={16} /> 机器人麦克风与扬声器
          </p>
          <span>以上连接同步使用机器人地址，端口和拾音通道保持原配置。</span>
        </div>
        {!!connection?.independent_media.length && (
          <p className="panel-footnote">
            {connection.independent_media.join("、")}
            配置了独立服务器地址，将保留该地址。需要跟随机器人时可在配置中使用{" "}
            {"{robot_ip}"}。
          </p>
        )}
      </section>

      <section className="card connection-cli-card">
        <div>
          <h3>也可以从终端切换</h3>
          <p>替换为机器人实际 IP，一条命令保存并启动或更新现有连接。</p>
        </div>
        <code>./start.sh --robot-ip {address.trim() || "192.168.4.1"}</code>
      </section>
    </div>
  );
}
