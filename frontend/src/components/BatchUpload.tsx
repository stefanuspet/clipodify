import { useEffect, useState } from "react"
import { ListPlus, Loader2, CalendarClock, Upload } from "lucide-react"
import { api } from "../lib/api"

type Cand = { render_id: string; title: string; created_at: string; file: string; intent: string | null }
type Item = { render_id: string; title: string; publish_local: string; reason: string }

export function BatchUpload({ canUpload, onStarted }: { canUpload: boolean; onStarted?: () => void }) {
  const [cands, setCands] = useState<Cand[]>([])
  const [max, setMax] = useState(5)
  const [picked, setPicked] = useState<string[]>([])
  const [items, setItems] = useState<Item[] | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  async function load() {
    const { data } = await api.get("/api/youtube/batch/candidates")
    setCands(data.renders); setMax(data.max)
  }
  useEffect(() => { load() }, [])

  function toggle(id: string) {
    setItems(null)
    setPicked(p => p.includes(id) ? p.filter(x => x !== id) : p.length >= max ? p : [...p, id])
  }

  async function preview() {
    setBusy(true); setMsg("")
    try {
      const { data } = await api.post("/api/youtube/batch/preview", { render_ids: picked })
      setItems(data.items); setNotes(data.notes)
    } catch (e: any) { setMsg(e?.response?.data?.detail ?? "Gagal menyusun jadwal.") }
    finally { setBusy(false) }
  }

  async function start() {
    if (!items) return
    setBusy(true); setMsg("")
    try {
      await api.post("/api/youtube/batch", { items })
      setMsg(`${items.length} video masuk antrean upload — diupload satu per satu, pantau di daftar di bawah.`)
      setItems(null); setPicked([]); await load(); onStarted?.()
    } catch (e: any) { setMsg(e?.response?.data?.detail ?? "Gagal memulai antrean.") }
    finally { setBusy(false) }
  }

  const inp = "bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs"
  return (
    <div className="card space-y-3">
      <h2 className="font-semibold flex items-center gap-2"><ListPlus size={16} /> Jadwalkan beberapa klip</h2>
      {cands.length === 0 ? (
        <p className="text-sm text-zinc-500">Tidak ada render Auto Edit yang belum diupload.</p>
      ) : (
        <>
          <p className="text-xs text-zinc-500">
            Pilih maks. {max} render (batas kuota upload harian). Jadwal dibagikan otomatis berjarak ≥2 jam dari video lain.
          </p>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {cands.map(c => (
              <label key={c.render_id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={picked.includes(c.render_id)} onChange={() => toggle(c.render_id)}
                  disabled={!picked.includes(c.render_id) && picked.length >= max} />
                <span className="flex-1 truncate text-zinc-200">{c.title || c.file}</span>
                <span className="text-xs text-zinc-500 whitespace-nowrap">{c.intent} · {c.created_at.slice(0, 16).replace("T", " ")}</span>
              </label>
            ))}
          </div>
          {!items && (
            <button className="btn-ghost text-sm" onClick={preview} disabled={busy || picked.length === 0}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <CalendarClock size={14} />} Atur jadwal ({picked.length})
            </button>
          )}
        </>
      )}

      {items && (
        <div className="space-y-2 border-t border-zinc-800 pt-2">
          {items.map((it, k) => (
            <div key={it.render_id} className="flex flex-wrap items-center gap-2">
              <input className={`${inp} flex-1 min-w-[220px]`} value={it.title} maxLength={100}
                onChange={e => setItems(items.map((x, j) => j === k ? { ...x, title: e.target.value } : x))} />
              <input type="datetime-local" className={inp} value={it.publish_local} title={it.reason}
                onChange={e => setItems(items.map((x, j) => j === k ? { ...x, publish_local: e.target.value } : x))} />
            </div>
          ))}
          {notes.map((n, i) => <p key={i} className="text-[11px] text-zinc-500">{n}</p>)}
          <div className="flex gap-2">
            <button className="btn-primary" onClick={start} disabled={busy || !canUpload}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload & jadwalkan semua
            </button>
            <button className="btn-ghost text-sm" onClick={() => setItems(null)}>Batal</button>
          </div>
          {!canUpload && <p className="text-xs text-amber-400">Izin upload belum aktif — klik "Hubungkan ulang" di atas.</p>}
        </div>
      )}
      {msg && <p className="text-xs text-brand-400">{msg}</p>}
    </div>
  )
}
