import { Icon } from "./Icons";
import { timeLabel } from "./api";
import type { RobotState, TelemetryField } from "./types";

export function getField(state: RobotState | null, group: string, key: string) {
  return state?.telemetry[group]?.find((field) => field.key === key);
}

export function valueLabel(field?: TelemetryField) {
  if (!field || field.value === null || field.availability !== "fresh")
    return "—";
  if (typeof field.value === "boolean") return field.value ? "是" : "否";
  if (typeof field.value === "number")
    return Number.isInteger(field.value)
      ? String(field.value)
      : field.value.toFixed(2);
  if (typeof field.value === "object") return JSON.stringify(field.value);
  return String(field.value) || "无";
}

const groupNames: Record<string, string> = {
  battery: "电池与电源",
  motion: "运动与控制源",
  audio: "音频状态",
  joints: "关节遥测",
  interaction: "触摸与交互",
  system: "系统信息",
};

export function Telemetry({
  state,
  stale,
}: {
  state: RobotState | null;
  stale: boolean;
}) {
  return (
    <div className="telemetry-grid">
      {Object.entries(state?.telemetry ?? {}).map(([group, fields]) => (
        <section className="card telemetry-card" key={group}>
          <div className="card-heading">
            <h2>
              <Icon name={group === "battery" ? "battery" : "activity"} />
              {groupNames[group] ?? group}
            </h2>
            <span className="mono small muted">
              {
                fields.filter((f) => f.availability === "fresh" && !stale)
                  .length
              }{" "}
              / {fields.length} FIELDS
            </span>
          </div>
          <div className="field-list">
            {fields.map((field, index) => (
              <div className="field-row" key={`${field.key}-${index}`}>
                <div>
                  <span>{field.label}</span>
                  <small title={field.source}>{field.key}</small>
                </div>
                <div className="field-value">
                  <strong>
                    {stale ? "—" : valueLabel(field)}{" "}
                    <small>{field.unit}</small>
                  </strong>
                  <small
                    className={
                      field.availability === "fresh" && !stale
                        ? "green"
                        : "muted"
                    }
                    title={field.reason ?? field.source}
                  >
                    {stale
                      ? "服务连接已断开"
                      : field.availability === "fresh"
                        ? timeLabel(field.observed_at)
                        : field.availability === "stale"
                          ? "数据已过期"
                          : "尚未收到数据"}
                  </small>
                </div>
              </div>
            ))}
          </div>
          {fields.every((field) => field.availability !== "fresh") && (
            <p className="field-note">
              {fields[0]?.reason ?? "等待该接口的真实数据。"}
            </p>
          )}
        </section>
      ))}
      {!Object.keys(state?.telemetry ?? {}).length && (
        <div className="card empty-state">
          <Icon name="activity" size={36} />
          <h3>等待设备遥测</h3>
          <p>
            {state?.error ?? "机器人连接后，状态、单位和更新时间会显示在这里。"}
          </p>
        </div>
      )}
    </div>
  );
}
