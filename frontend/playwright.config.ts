import { defineConfig } from "@playwright/test";

// Serves the built dashboard from the FastAPI process with a throwaway database.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL: "http://localhost:8765", trace: "retain-on-failure" },
  webServer: {
    command: "cd .. && rm -f data/e2e.db && DATABASE_URL=sqlite:///data/e2e.db .venv/bin/uvicorn backend.app.api.main:app --port 8765",
    url: "http://localhost:8765/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
