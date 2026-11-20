import { useEffect, useRef, useState } from "react"
import { Inbox, Loader2, Upload, Play, Pause, Check } from "lucide-react"
import { api } from "../../lib/api"

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

type InboxFile = { name: string; size_kb: number; duration_s: number; duplicate_of: string | null }
type InboxData = { files: InboxFile[]; licenses: Record<string, string>; moods: string[]; sfx_tags: string[] }
type Form = {
  kind: "sfx" | "music"
  tag: string; name: string; use: string; intensity: string; riser: boolean
  title: string; mood: string[]; energy: string
  license: string; source: string; url: string; attribution: string; notes: string
}

const EMPTY: Form = {
  kind: "sfx", tag: "", name: "", use: "", intensity: "mid", riser: false,
  title: "", mood: [], energy: "mid",
  license: "youtube_audio_library", source: "", url: "", attribution: "", notes: "",
}

export function AudioImportSection({ onImported }: { onImported?: () => void }) {
  const [data, setData] = useState<InboxData | null>(null)
  const [forms, setForms] = useState<Record<string, Form>>({})
  const [busy, setBusy] = useState<string>("")
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [playing, setPlaying] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function load() {
    const { data: d } = await api.get("/api/editor/assets/inbox")
    setData(d)
    setForms(prev => Object.fromEntries(d.files.map((f: InboxFile) =>
      [f.name, prev[f.name] ?? { ...EMPTY, name: f.name.replace(/\.[^.]+$/, ""), title: f.name.replace(/\.[^.]+$/, "") }])))
  }
  useEffect(() => { load() }, [])

  function upd(name: string, patch: Partial<Form>) {
    setForms(f => ({ ...f, [name]: { ...f[name], ...patch } }))
  }

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    setBusy("upload")
    try {
      for (const file of files) {
        const form = new FormData(); form.append("file", file)
        await api.post("/api/editor/sfx/upload", form, { headers: { "Content-Type": "multipart/form-data" } })
      }
      await load()
    } finally { setBusy(""); if (fileRef.current) fileRef.current.value = "" }
  }

  function toggle(name: string) {
    if (playing === name) { audioRef.current?.pause(); setPlaying(null); return }
    audioRef.current?.pause()
    const a = new Audio(`${API}/api/editor/assets/inbox/${encodeURIComponent(name)}`)
    a.onended = () => setPlaying(null); a.play().catch(() => {}); audioRef.current = a; setPlaying(name)
  }

  async function doImport(name: string) {
    const f = forms[name]
    setBusy(name); setMsg(m => ({ ...m, [name]: "" }))
    try {
      const info = f.kind === "sfx"
        ? { tag: f.tag, name: f.name, use: f.use, intensity: f.intensity, align: f.riser ? "end" : null }
        : { title: f.title, mood: f.mood, energy: f.energy }
      const license = { kind: f.license, source: f.source, url: f.url, attribution_text: f.attribution,
        attribution_required: !!f.attribution.trim(), notes: f.notes }
      await api.post("/api/editor/assets/import", { name, kind: f.kind, info, license })
      await load(); onImported?.()
    } catch (e: any) {
      setMsg(m => ({ ...m, [name]: e?.response?.data?.detail ?? "Gagal impor." }))
    } finally { setBusy("") }
  }

  if (!data) return null
  const inp = "bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs"

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Inbox size={14} /> Impor Audio
          {data.files.length > 0 && <span className="badge bg-zinc-800 text-zinc-400">{data.files.length}</span>}
        </h2>
        <label className={`btn-ghost text-xs cursor-pointer ${busy === "upload" ? "opacity-50 pointer-events-none" : ""}`}>
          {busy === "upload" ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} Unggah ke kotak masuk
          <input ref={fileRef} type="file" multiple accept=".mp3,.wav,.ogg,.m4a,.flac" className="hidden" onChange={upload} />
        </label>
      </div>
      <p className="text-xs text-zinc-500">
        File masuk kotak masuk dulu (atau taruh di <code>assets/inbox/</code>). Masuk library hanya setelah lisensinya diisi.
        Lisensi NC (non-komersial) dan ND (tanpa turunan) ditolak.
      </p>
      {data.files.length === 0 && <p className="text-sm text-zinc-500">Kotak masuk kosong.</p>}

      {data.files.map(file => {
        const f = forms[file.name]; if (!f) return null
        return (
          <div key={file.name} className="border border-zinc-800 rounded-lg p-3 space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <button onClick={() => toggle(file.name)} className="w-7 h-7 rounded-full bg-zinc-800 flex items-center justify-center">
                {playing === file.name ? <Pause size={12} /> : <Play size={12} />}
              </button>
              <span className="text-zinc-200 text-sm">{file.name}</span>
              <span className="text-zinc-500">{file.duration_s}s · {file.size_kb} KB</span>
              {file.duplicate_of && <span className="text-amber-400">sudah ada: {file.duplicate_of}</span>}
            </div>

            <div className="flex flex-wrap gap-2 items-center">
              <select className={inp} value={f.kind} onChange={e => upd(file.name, { kind: e.target.value as Form["kind"] })}>
                <option value="sfx">SFX</option><option value="music">Musik</option>
              </select>
              {f.kind === "sfx" ? <>
                <input className={inp} list="sfx-tags" placeholder="tag (whoosh, hit, laugh…)" value={f.tag}
                  onChange={e => upd(file.name, { tag: e.target.value })} />
                <datalist id="sfx-tags">{data.sfx_tags.map(t => <option key={t} value={t} />)}</datalist>
                <input className={`${inp} flex-1 min-w-[180px]`} placeholder="kapan dipakai (dibaca Gemini)" value={f.use}
                  onChange={e => upd(file.name, { use: e.target.value })} />
                <select className={inp} value={f.intensity} onChange={e => upd(file.name, { intensity: e.target.value })}>
                  <option value="low">ringan</option><option value="mid">sedang</option><option value="high">kuat</option>
                </select>
                <label className="flex items-center gap-1 text-zinc-400">
                  <input type="checkbox" checked={f.riser} onChange={e => upd(file.name, { riser: e.target.checked })} /> riser
                </label>
              </> : <>
                <input className={`${inp} flex-1 min-w-[180px]`} placeholder="judul lagu" value={f.title}
                  onChange={e => upd(file.name, { title: e.target.value })} />
                <select className={inp} value={f.energy} onChange={e => upd(file.name, { energy: e.target.value })}>
                  <option value="low">energi rendah</option><option value="mid">sedang</option><option value="high">tinggi</option>
                </select>
                <span className="flex flex-wrap gap-1">
                  {data.moods.map(m => (
                    <button key={m} onClick={() => upd(file.name, { mood: f.mood.includes(m) ? f.mood.filter(x => x !== m) : [...f.mood, m] })}
                      className={`px-2 py-0.5 rounded border ${f.mood.includes(m) ? "border-brand-500 text-brand-300" : "border-zinc-700 text-zinc-500"}`}>
                      {m}
                    </button>
                  ))}
                </span>
              </>}
            </div>

            <div className="flex flex-wrap gap-2 items-center">
              <select className={inp} value={f.license} onChange={e => upd(file.name, { license: e.target.value })}>
                {Object.entries(data.licenses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <input className={`${inp} flex-1 min-w-[160px]`} placeholder="URL sumber (disarankan)" value={f.url}
                onChange={e => upd(file.name, { url: e.target.value })} />
              <input className={`${inp} flex-1 min-w-[200px]`}
                placeholder={f.license === "cc_by" ? "teks kredit (WAJIB)" : "teks kredit (bila sumber mewajibkan)"}
                value={f.attribution} onChange={e => upd(file.name, { attribution: e.target.value })} />
              {f.license === "other" && <input className={`${inp} flex-1`} placeholder="jelaskan lisensinya" value={f.notes}
                onChange={e => upd(file.name, { notes: e.target.value })} />}
              <button className="btn-primary text-xs" disabled={busy !== "" || !!file.duplicate_of} onClick={() => doImport(file.name)}>
                {busy === file.name ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Impor
              </button>
            </div>
            {msg[file.name] && <p className="text-red-400">{msg[file.name]}</p>}
          </div>
        )
      })}
    </section>
  )
}
