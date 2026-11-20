import { useEffect, useState } from "react"
import { CalendarClock, ExternalLink } from "lucide-react"
import { api } from "../lib/api"

type Row = {
  id: number; title: string; status: string; progress: number; video_id: string | null
  publish_at: string | null; error: string | null; created_at: string
}

const STATUS: Record<string, string> = {
  queued: "antre", uploading: "mengupload", uploaded: "private", scheduled: "terjadwal", failed: "gagal",
}

export function UploadQueue() {
  const [rows, setRows] = useState<Row[]>([])
  useEffect(() => {
    const load = () => api.get("/api/youtube/uploads").then(r => setRows(r.data)).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="card space-y-2">
      <h2 className="font-semibold flex items-center gap-2"><CalendarClock size={16} /> Upload & jadwal</h2>
      {rows.length === 0 && <p className="text-sm text-zinc-500">Belum ada upload. Upload dari halaman Editor setelah render.</p>}
      {rows.map(r => (
        <div key={r.id} className="flex items-center gap-3 text-sm border-b border-zinc-800 py-1.5">
          <span className={`text-xs w-20 shrink-0 ${r.status === "failed" ? "text-red-400" : r.status === "scheduled" ? "text-green-400" : "text-zinc-400"}`}>
            {STATUS[r.status] ?? r.status}{r.status === "uploading" ? ` ${Math.round(r.progress * 100)}%` : ""}
          </span>
          <span className="flex-1 truncate text-zinc-200" title={r.error ?? ""}>{r.title}</span>
          <span className="text-xs text-zinc-500 whitespace-nowrap">
            {r.publish_at ? `tayang ${new Date(r.publish_at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}` : ""}
          </span>
          {r.video_id && (
            <a href={`https://studio.youtube.com/video/${r.video_id}/edit`} target="_blank" rel="noreferrer"
              className="text-brand-400"><ExternalLink size={13} /></a>
          )}
        </div>
      ))}
    </div>
  )
}
