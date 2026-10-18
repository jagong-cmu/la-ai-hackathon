import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    env: {
      CARTWISE_MODE: "fixture",
    },
  },
});
