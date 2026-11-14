import { defineConfig } from "@playwright/test";

const preview = process.env.PLAYWRIGHT_PREVIEW === "1";

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    timeout: 30_000,
    expect: { timeout: 10_000 },
    reporter: "list",
    use: {
        baseURL: "http://127.0.0.1:4179",
        channel: process.env.PLAYWRIGHT_CHANNEL,
        viewport: { width: 1440, height: 1000 },
        screenshot: process.env.PLAYWRIGHT_SCREENSHOT === "on" ? "on" : "only-on-failure",
        trace: "retain-on-failure",
    },
    webServer: {
        command: `npm run ${preview ? "preview" : "dev"} -- --host 127.0.0.1 --port 4179 --strictPort`,
        url: "http://127.0.0.1:4179",
        reuseExistingServer: false,
    },
});
