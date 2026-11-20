import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { Tv2, Loader2, CalendarClock, Upload, ExternalLink, AlertTriangle } from "lucide-react"
import { api } from "../../lib/api"

type Defaults = {
  title: string; description: string; tags: string[]; music_credit: string | null; file: string
  connected: boolean; upload_scope: boolean; tz_offset_hours: number
}
type Slot = { local: string; reason: string }
type UploadRow = { id: number; status: string; progress: number; video_id: string | null; publish_at: string | null; error: string | null }

/** Waktu lokal input (yyyy-MM-ddTHH:mm, zona channel) → ISO dengan offset. */
function toIso(local: string, tz: number) {
  const sign = tz >= 0 ? "+" : "-"
  const hh = String(Math.abs(tz)).padStart(2, "0")
  return `${local}:00${sign}${hh}:00`
}

function defaultSlot(tz: number) {
  const now = new Date(Date.now() + tz * 3600_000)          // "sekarang" di zona channel
  const d = new Date(now); d.setUTCHours(19, 0, 0, 0)
  if (d.getTime() - now.getTime() < 60 * 60_000) d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 16)
}

export function UploadPanel({ renderId }: { renderId: string }) {
  const [d, setD] = useState<Defaults | null>(null)
  const [err, setErr] = useState("")
  const [title, setTitle] = useState("")
  const [desc, setDesc] = useState("")
  const [tags, setTags] = useState("")
  const [schedule, setSchedule] = useState(true)
  const [when, setWhen] = useState("")
  const [row, setRow] = useState<UploadRow | null>(null)
  const [slots, setSlots] = useState<Slot[]>([])
  const [notes, setNotes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    api.get("/api/youtube/upload/defaults", { params: { render_id: renderId } })
      .then(r => {
        setD(r.data); setTitle(r.data.title); setDesc(r.data.description)
        setTags(r.data.tags.join(", ")); setWhen(defaultSlot(r.data.tz_offset_hours))
        api.get("/api/youtube/schedule/suggest").then(s => {
          setSlots(s.data.slots); setNotes(s.data.notes)
          if (s.data.slots[0]) setWhen(s.data.slots[0].local)
        }).catch(() => {})
      })
      .catch(e => setErr(e?.response?.data?.detail ?? "Gagal memuat data upload."))
    return () => { if (poll.current) clearInterval(poll.current) }
  }, [renderId])

  async function start() {
    if (!d) return
    setBusy(true); setErr("")
    try {
      const { data } = await api.post("/api/youtube/upload", {
        render_id: renderId, title, description: desc,
        tags: tags.split(",").map(t => t.trim()).filter(Boolean),
        publish_at: schedule ? toIso(when, d.tz_offset_hours) : null,
      })
      poll.current = setInterval(async () => {
        const { data: list } = await api.get("/api/youtube/uploads")
        const u = (list as UploadRow[]).find(x => x.id === data.upload_id)
        if (!u) return
        setRow(u)
        if (["uploaded", "scheduled", "failed"].includes(u.status)) {
          if (poll.current) clearInterval(poll.current)
          setBusy(false)
        }
      }, 1500)
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Gagal memulai upload."); setBusy(false)
    }
  }

  if (err && !d) return <div className="card text-sm text-red-400">{err}</div>
  if (!d) return null
  const inp = "w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm"
  const done = row && ["uploaded", "scheduled"].includes(row.status)

  return (
    <div className="card space-y-3">
      <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
        <Tv2 size={15} className="text-red-500" /> Upload ke YouTube
      </h2>

      {!d.connected || !d.upload_scope ? (
        <p className="text-sm text-amber-400 flex gap-2">
          <AlertTriangle size={15} className="shrink-0 mt-0.5" />
          <span>
            {d.connected ? "Akun YouTube belum memberi izin upload." : "Belum terhubung ke YouTube."}{" "}
            <Link to="/youtube" className="underline">Hubungkan (ulang) di halaman YouTube</Link> — sekali saja.
          </span>
        </p>
      ) : done ? (
        <div className="text-sm space-y-1">
          <p className="text-green-400">
            {row!.status === "scheduled"
              ? `Terjadwal tayang ${new Date(row!.publish_at!).toLocaleString("id-ID")} (masih private sampai jadwal).`
              : "Terupload sebagai PRIVATE."}
          </p>
          <a href={`https://studio.youtube.com/video/${row!.video_id}/edit`} target="_blank" rel="noreferrer"
            className="text-brand-400 hover:underline flex items-center gap-1">
            Buka di YouTube Studio <ExternalLink size={12} />
          </a>
          <p className="text-xs text-zinc-500">Otomatis terhubung ke halaman Learning.</p>
        </div>
      ) : (
        <>
          <input className={inp} value={title} maxLength={100} onChange={e => setTitle(e.target.value)} placeholder="Judul" />
          <textarea className={inp} rows={4} value={desc} onChange={e => setDesc(e.target.value)} placeholder="Deskripsi" />
          <input className={inp} value={tags} onChange={e => setTags(e.target.value)} placeholder="tag, dipisah koma" />
          {d.music_credit && <p className="text-xs text-amber-400">Kredit musik sudah ditambahkan ke deskripsi.</p>}
          <label className="flex items-center gap-2 text-sm text-zinc-300">
            <input type="checkbox" checked={schedule} onChange={e => setSchedule(e.target.checked)} />
            <CalendarClock size={14} /> Jadwalkan tayang
          </label>
          {schedule && (
            <div className="flex items-center gap-2 text-sm">
              <input type="datetime-local" className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5"
                value={when} onChange={e => setWhen(e.target.value)} />
              <span className="text-zinc-500">zona UTC{d.tz_offset_hours >= 0 ? "+" : ""}{d.tz_offset_hours}</span>
            </div>
          )}
          {schedule && slots.length > 0 && (
            <div className="space-y-1">
              <div className="flex flex-wrap gap-1.5">
                {slots.map(s => (
                  <button key={s.local} title={s.reason} onClick={() => setWhen(s.local)}
                    className={`text-xs px-2 py-1 rounded border ${when === s.local ? "border-brand-500 text-brand-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"}`}>
                    {new Date(s.local).toLocaleString("id-ID", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                  </button>
                ))}
              </div>
              {notes.map((n, i) => <p key={i} className="text-[11px] text-zinc-500 leading-snug">{n}</p>)}
            </div>
          )}
          <p className="text-xs text-zinc-500">
            Video selalu naik sebagai PRIVATE. Selama proyek Google Cloud Anda belum lolos audit YouTube API,
            video hasil upload API tetap terkunci private — jadwal publik baru berjalan setelah audit lolos.
          </p>
          <button className="btn-primary" onClick={start} disabled={busy || !title.trim()}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            {busy ? `Mengupload… ${Math.round((row?.progress ?? 0) * 100)}%` : schedule ? "Upload & jadwalkan" : "Upload (private)"}
          </button>
          {row?.status === "failed" && <p className="text-sm text-red-400">{row.error}</p>}
          {err && <p className="text-sm text-red-400">{err}</p>}
        </>
      )}
    </div>
  )
}
