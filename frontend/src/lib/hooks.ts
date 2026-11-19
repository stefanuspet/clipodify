import { useEffect, useRef, useState } from "react"
import { WS_BASE } from "./api"

export type WsEvent =
  | { type: "progress"; step: number; total: number; label: string; detail?: string }
  | { type: "done" }
  | { type: "error"; message: string }

export function useJobWs(jobId: string | null) {
  const [events, setEvents] = useState<WsEvent[]>([])
  const [latest, setLatest] = useState<WsEvent | null>(null)
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!jobId) return
    const url = `${WS_BASE}/api/jobs/ws/${jobId}`
    ws.current = new WebSocket(url)
    ws.current.onmessage = (e) => {
      const ev: WsEvent = JSON.parse(e.data)
      setEvents((prev) => [...prev, ev])
      setLatest(ev)
    }
    return () => { ws.current?.close() }
  }, [jobId])

  return { events, latest }
}

export function useRenderWs(renderId: string | null) {
  const [events, setEvents] = useState<WsEvent[]>([])
  const [latest, setLatest] = useState<WsEvent | null>(null)
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!renderId) return
    const url = `${WS_BASE}/api/editor/ws/render/${renderId}`
    ws.current = new WebSocket(url)
    ws.current.onmessage = (e) => {
      const ev: WsEvent = JSON.parse(e.data)
      setEvents((prev) => [...prev, ev])
      setLatest(ev)
    }
    return () => { ws.current?.close() }
  }, [renderId])

  return { events, latest }
}
