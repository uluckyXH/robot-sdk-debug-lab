import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8091",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "../.venv/bin/python ../tests/serve_web.py",
    // Waiting for the local process avoids inherited HTTP proxy settings in URL probes.
    wait: { stderr: /Uvicorn running on http:\/\/127\.0\.0\.1:8091/ },
    reuseExistingServer: false,
    timeout: 15000,
  },
});
