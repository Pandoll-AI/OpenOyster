import { defineConfig } from "vite";

export default defineConfig({
  server: { port: 4319 },
  build: { target: "es2022" },
});
