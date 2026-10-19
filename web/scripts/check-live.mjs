// Browser acceptance on a real robot. Only reads, video, and audio are permitted.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

const url = "http://127.0.0.1:8090";
const health = await fetch(`${url}/api/health`).then((r) => r.json());
if (health.read_only !== true)
  throw new Error("Live acceptance requires read-only mode");
mkdirSync("../logs", { recursive: true });
const browser = await chromium.launch({ headless: true });
const report = { errors: [], forbiddenWrites: [], audio: null };
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
    deviceScaleFactor: 1,
  });
  page.on("pageerror", (error) => report.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    if (
      request.method() !== "GET" &&
      /\/api\/(commands|motion)/.test(request.url())
    ) {
      report.forbiddenWrites.push(request.url());
      return route.abort();
    }
    return route.continue();
  });
  await page.addInitScript(() => {
    const connect = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function (destination, ...rest) {
      const result = connect.call(this, destination, ...rest);
      if (
        this instanceof GainNode &&
        destination instanceof AudioDestinationNode
      ) {
        const analyser = this.context.createAnalyser();
        connect.call(this, analyser);
        window.audioProbe = { analyser, context: this.context };
      }
      return result;
    };
  });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.locator(".live-label.active").waitFor({ timeout: 20000 });
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole("button", { name: "开启声音监听" }).click();
  await page.locator(".audio-card.listening").waitFor({ timeout: 15000 });
  await page.waitForTimeout(2500);
  report.audio = await page.evaluate(() => {
    const probe = window.audioProbe;
    const samples = new Float32Array(probe.analyser.fftSize);
    probe.analyser.getFloatTimeDomainData(samples);
    return {
      context: probe.context.state,
      sampleRate: probe.context.sampleRate,
      outputPeak: Math.max(...samples.map(Math.abs)),
      nonzero: samples.filter((v) => v !== 0).length,
    };
  });
  await page.screenshot({
    path: "../logs/dashboard-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "结束监听" }).click();
  await page.getByRole("button", { name: "右目", exact: true }).click();
  await page.locator(".live-label.active").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "设备信息", exact: true }).click();
  await page.screenshot({
    path: "../logs/telemetry-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "工作台", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".live-label.active").waitFor({ timeout: 20000 });
  await page.screenshot({
    path: "../logs/dashboard-mobile.png",
    fullPage: true,
  });
  report.mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  report.motionButtonDisabled = await page
    .getByRole("button", { name: "前进", exact: true })
    .isDisabled();
  await page.close();
} finally {
  await browser.close();
  writeFileSync("../logs/browser-live.json", JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
if (
  report.errors.length ||
  report.forbiddenWrites.length ||
  report.mobileOverflow ||
  !report.audio?.outputPeak
)
  process.exitCode = 1;
