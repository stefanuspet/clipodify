import axios from "axios"

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? "http://localhost:8000",
  timeout: 60_000,
})

export const WS_BASE =
  (import.meta.env.VITE_API_URL ?? "http://localhost:8000")
    .replace(/^http/, "ws")
