import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";


const backendPort = Number(process.env.ELVERN_PORT || 8000);
const configuredBackendHost = process.env.ELVERN_BIND_HOST || "127.0.0.1";
const backendHost =
  configuredBackendHost === "0.0.0.0" || configuredBackendHost === "::" || configuredBackendHost === "[::]"
    ? "127.0.0.1"
    : configuredBackendHost;


export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": `http://${backendHost}:${backendPort}`,
      "/health": `http://${backendHost}:${backendPort}`,
    },
  },
  preview: {
    host: process.env.ELVERN_FRONTEND_HOST || "127.0.0.1",
    port: Number(process.env.ELVERN_FRONTEND_PORT || 4173),
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
