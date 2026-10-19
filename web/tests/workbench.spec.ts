import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const jpeg = readFileSync("../tests/fixtures/frame.jpg");

test.beforeEach(async ({ page }) => {
  // Never attempt RTSP, even against the in-memory fixture backend.
  await page.route("**/api/video/stream?*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: '{"detail":"offline test fixture"}',
    }),
  );
});

test("readonly controls stay locked and telemetry navigation works", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "机器人工作台." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "前进", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "双足站立", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "语音与表情", exact: true }).click();
  await expect(page.getByRole("button", { name: "发送播报" })).toBeDisabled();
  await page.getByRole("button", { name: "设备信息", exact: true }).click();
  await expect(page.getByRole("heading", { name: "设备遥测." })).toBeVisible();
  await page.getByRole("button", { name: "查看原始 JSON" }).click();
  await expect(page.locator(".json-view")).toContainText('"read_only": true');
  expect(errors).toEqual([]);
});

test("SDK capture is stored, opens in gallery and downloads", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "SDK 单帧", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("SDK 单帧已保存");
  await page.getByRole("button", { name: "截图相册", exact: true }).click();
  await page.locator(".gallery-item").first().click();
  await expect(page.locator("dialog")).toBeVisible();
  expect(
    await page
      .locator("dialog>img")
      .evaluate((image: HTMLImageElement) => image.naturalWidth),
  ).toBe(64);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载原图" }).click();
  expect((await download).suggestedFilename()).toMatch(/t1-left-.*\.jpg/);
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog")).toHaveCount(0);
});

test("audio worklet receives PCM, shows levels, and releases the socket", async ({
  page,
}) => {
  await page.route("**/api/state", async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    state.audio.configured = true;
    await route.fulfill({ response, json: state });
  });
  let closed = false;
  await page.routeWebSocket("**/api/audio", (socket) => {
    socket.send(
      JSON.stringify({
        type: "format",
        sample_rate: 16000,
        channels: 1,
        format: "s16le",
      }),
    );
    const chunk = new Int16Array(320);
    for (let i = 0; i < chunk.length; i++)
      chunk[i] = Math.round(Math.sin(i / 9) * 1000);
    const timer = setInterval(() => socket.send(Buffer.from(chunk.buffer)), 20);
    socket.onMessage(() => undefined);
    socket.onClose(() => {
      closed = true;
      clearInterval(timer);
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "开启声音监听" }).click();
  await expect(page.locator(".audio-state")).toHaveText("正在监听");
  await expect(page.locator(".audio-spec")).not.toContainText("— dBFS");
  await page.getByRole("button", { name: "结束监听" }).click();
  await expect(page.locator(".audio-state")).toHaveText("待机");
  await expect.poll(() => closed).toBe(true);
});

test("mobile layout does not overflow and video failure can retry", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("视频暂未连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新连接" }).click();
  await page.getByRole("button", { name: "右目", exact: true }).click();
  await expect(page.locator(".camera-label")).toContainText("RIGHT");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "SDK 调用台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "SDK 调用台." }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("offline backend shows connection error and cannot enable controls", async ({
  page,
}) => {
  await page.route("**/api/state", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("后端连接中断");
  await expect(
    page.getByRole("button", { name: "停止本次运动" }),
  ).toBeDisabled();
});
