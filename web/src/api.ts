export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ?? AbortSignal.timeout(20000),
  });
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export const timeLabel = (value?: string | null) =>
  value ? new Date(value).toLocaleTimeString("zh-CN", { hour12: false }) : "—";
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作未完成";
