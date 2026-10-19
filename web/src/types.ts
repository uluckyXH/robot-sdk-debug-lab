export interface TelemetryField {
  key: string;
  label: string;
  value: unknown;
  unit?: string;
  source: string;
  observed_at?: string;
  availability: "fresh" | "stale" | "unknown" | "unavailable";
  reason?: string;
  scale_verified?: boolean;
}
export interface VideoStatus {
  status: "idle" | "connecting" | "live" | "error";
  frames: number;
  received_at: string | null;
  error: string | null;
  fps: number;
}
export interface RobotState {
  name: string;
  family: string;
  robot_ip: string;
  ros_domain_id: number;
  read_only: boolean;
  sdk_ready: boolean;
  connected: boolean;
  error: string | null;
  observed_at: string;
  sdk_commit: string;
  command_epoch: string;
  telemetry: Record<string, TelemetryField[]>;
  audio: {
    configured: boolean;
    active: boolean;
    sample_rate: number;
    channels: number;
    channel: number | null;
    device: string | null;
  };
  video: Record<"left" | "right", VideoStatus>;
}
export interface Action {
  action: string;
  label: string;
  kind: string;
  available: boolean;
  reason?: string;
}
export interface Capability {
  name: string;
  available: boolean;
  reason: string;
}
export interface Capabilities {
  read_only: boolean;
  interfaces: Capability[];
  motion: {
    actions: Action[];
    current: Receipt | null;
    stop: { available: boolean; reason?: string };
  };
}
export interface Snapshot {
  id: string;
  camera: string;
  source: string;
  created_at: string;
  width: number;
  height: number;
  bytes: number;
  url: string;
}
export interface LogEvent {
  id: string;
  time: string;
  operation: string;
  status: string;
  detail: string;
  elapsed_ms?: number;
}
export interface Receipt {
  command_id: string;
  status: string;
  detail: string;
  output_finished?: boolean;
  execution_confirmed?: boolean;
  [key: string]: unknown;
}

export interface SdkInterface {
  id: string;
  label: string;
  category: string;
  kind: "service" | "publish" | "subscribe" | "transport";
  write: boolean;
  path: string;
  type: string;
  schema?: string;
  template: Record<string, unknown>;
  rate_hz?: number;
  note?: string;
  source: string;
  native_type_available: boolean;
}

export interface JointLimit {
  name: string;
  minimum: number;
  maximum: number;
  max_effort: number;
  upper: boolean;
}

export interface SdkCatalog {
  sdk_commit: string;
  entries: SdkInterface[];
  joints: JointLimit[];
  definitions: Record<
    string,
    {
      fields: { name: string; type: string; description?: string }[];
      constants: Record<string, string | number>;
    }
  >;
}

export interface TopicSample {
  interface: string;
  availability: string;
  message: unknown;
  detail?: string;
  received_at?: string;
}
