import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { Type, Play, Save, Plus, Trash2, Sparkles, Star, Loader2, Copy } from "lucide-react"
import { api } from "../lib/api"

type Recipe = {
  name: string
  font: { family: string; weight: string }
  size: number; case: string; letter_spacing: number; words_per_line: number
  color_mode: string; fill_colors: string[]
  outline: { color: string; width: number }
  shadow: { color: string; opacity: number; distance: number }
  box: { enabled: boolean; color: string; opacity: number; padding: number }
  active_word: { mode: string; color: string; scale: number }
  highlight: { color: string; scale: number }
  animation: { type: string; from_scale: number; overshoot: number; duration_ms: number }
  position: string
}
type Style = { id: string; name: string; recipe: Recipe; created_by: string; favorite: boolean; notes: string }
type Options = {
  fonts: { family: string; category: string; weights: string[] }[]
  google_fonts_key: boolean
  anim_types: string[]; active_modes: string[]; color_modes: string[]; cases: string[]; positions: string[]
  default_recipe: Recipe
}
type Clip = { candidate_id: number; title: string; duration: number }

const LABEL: Record<string, string> = {
  pop: "Pop membesar", bounce: "Memantul", slide_up: "Geser naik", fade: "Fade", wipe: "Wipe", none: "Tanpa",
  single: "Satu warna", cycle_word: "Berganti per kata", cycle_line: "Berganti per baris",
  upper: "HURUF BESAR", lower: "huruf kecil", asis: "Sesuai ucapan",
  color: "Berubah warna", scale: "Membesar", chip: "Diberi blok warna",
  top: "Atas", center: "Tengah", bottom: "Bawah",
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span className="text-zinc-400 shrink-0">{label}</span>
      <span className="flex items-center gap-2 justify-end">{children}</span>
    </label>
  )
}

function Slider({ value, min, max, step, onChange, fmt }: {
  value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt?: (v: number) => string
}) {
  return (
    <>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))} className="w-36" />
      <span className="w-12 text-right font-mono text-xs text-zinc-300">{fmt ? fmt(value) : value}</span>
    </>
  )
}

function Select({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm">
      {options.map(o => <option key={o} value={o}>{LABEL[o] ?? o}</option>)}
    </select>
  )
}

function Color({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return <input type="color" value={value} onChange={e => onChange(e.target.value.toUpperCase())}
    className="w-8 h-7 bg-transparent border border-zinc-700 rounded" />
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs uppercase tracking-wide text-zinc-500">{title}</h3>
      {children}
    </div>
  )
}

export function StudioPage() {
  const [params] = useSearchParams()
  const [opts, setOpts] = useState<Options | null>(null)
  const [styles, setStyles] = useState<Style[]>([])
  const [clips, setClips] = useState<Clip[]>([])
  const [clipId, setClipId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [r, setR] = useState<Recipe | null>(null)
  const [favorite, setFavorite] = useState(true)
  const [notes, setNotes] = useState("")
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState<"" | "preview" | "save" | "gen">("")
  const [msg, setMsg] = useState("")
  const [desc, setDesc] = useState("")

  const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

  async function loadStyles() {
    const { data } = await api.get("/api/studio/styles")
    setStyles(data)
    return data as Style[]
  }

  useEffect(() => {
    Promise.all([api.get("/api/studio/options"), loadStyles(), api.get("/api/studio/clips")]).then(([o, st, c]) => {
      setOpts(o.data); setClips(c.data)
      if (c.data.length) setClipId(c.data[0].candidate_id)
      const want = params.get("style")
      const first = st.find(s => s.id === want) ?? st[0]
      if (first) pick(first)
      else setR(structuredClone(o.data.default_recipe))
    })
  }, [])

  function pick(s: Style) {
    setEditingId(s.id); setR(structuredClone(s.recipe)); setFavorite(s.favorite); setNotes(s.notes ?? "")
    setPreviewUrl(null); setMsg("")
  }

  function upd(path: string, value: any) {
    setR(prev => {
      if (!prev) return prev
      const next: any = structuredClone(prev)
      const keys = path.split(".")
      let o = next
      keys.slice(0, -1).forEach(k => { o = o[k] })
      o[keys[keys.length - 1]] = value
      return next
    })
  }

  async function preview() {
    if (!r) return
    setBusy("preview"); setMsg("")
    try {
      const { data } = await api.post("/api/studio/preview", { recipe: r, candidate_id: clipId })
      setPreviewUrl(`${API}${data.url}`)
      if (data.fixes?.length) setMsg("Disesuaikan supaya terbaca: " + data.fixes.join("; "))
    } catch (e: any) { setMsg(e?.response?.data?.detail ?? "Preview gagal.") }
    finally { setBusy("") }
  }

  async function save(asNew: boolean) {
    if (!r) return
    setBusy("save")
    try {
      const { data } = await api.post("/api/studio/styles", {
        id: asNew ? null : editingId, recipe: r, favorite, notes,
      })
      const st = await loadStyles()
      const s = st.find(x => x.id === data.id); if (s) pick(s)
      setMsg(asNew ? "Disimpan sebagai gaya baru." : "Tersimpan.")
    } finally { setBusy("") }
  }

  async function remove() {
    if (!editingId || !confirm("Hapus gaya ini? (dipindah ke folder .trash, bisa dikembalikan manual)")) return
    await api.delete(`/api/studio/styles/${editingId}`)
    const st = await loadStyles()
    if (st[0]) pick(st[0]); else { setEditingId(null); setR(structuredClone(opts!.default_recipe)) }
  }

  async function generate() {
    if (!desc.trim()) return
    setBusy("gen"); setMsg("")
    try {
      const { data } = await api.post("/api/studio/generate", { description: desc, base_style_id: editingId })
      setR(data.recipe); setEditingId(null); setFavorite(true); setNotes(`Dibuat Gemini dari: "${desc}"`)
      setPreviewUrl(null)
      setMsg("Resep dari Gemini dimuat (belum disimpan). Tekan Preview untuk melihat, lalu Simpan.")
    } catch (e: any) { setMsg(e?.response?.data?.detail ?? "Gemini gagal.") }
    finally { setBusy("") }
  }

  if (!opts || !r) return <div className="p-10 text-zinc-400 flex gap-2"><Loader2 className="animate-spin" size={16} /> Memuat…</div>
  const fontInfo = opts.fonts.find(f => f.family === r.font.family)
  const weights = fontInfo?.weights ?? [r.font.weight]

  return (
    <div className="max-w-6xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Type size={24} className="text-brand-500" /> Studio Subtitle
      </h1>
      {!opts.google_fonts_key && (
        <p className="text-xs text-amber-400">
          Google Fonts API key belum diisi — daftar font masih font dasar. Isi di <Link to="/settings" className="underline">Settings</Link> untuk ribuan font.
        </p>
      )}

      <div className="grid lg:grid-cols-[220px_1fr_360px] gap-4 items-start">
        {/* Daftar gaya */}
        <div className="card space-y-2">
          <button className="btn-primary w-full justify-center text-sm" onClick={() => {
            setEditingId(null); setR({ ...structuredClone(opts.default_recipe), name: "Gaya baru" }); setNotes(""); setPreviewUrl(null)
          }}>
            <Plus size={14} /> Gaya baru
          </button>
          {styles.map(s => (
            <button key={s.id} onClick={() => pick(s)}
              className={`w-full text-left text-sm px-2 py-1.5 rounded-lg border flex items-center gap-1.5
                ${editingId === s.id ? "border-brand-500 bg-brand-600/10" : "border-zinc-800 hover:border-zinc-600"}`}>
              {s.favorite && <Star size={12} className="text-yellow-400 shrink-0" />}
              {s.created_by === "gemini" && <Sparkles size={12} className="text-purple-400 shrink-0" />}
              <span className="truncate">{s.name}</span>
            </button>
          ))}
        </div>

        {/* Editor resep */}
        <div className="card space-y-5">
          <div className="flex gap-2">
            <input value={r.name} onChange={e => upd("name", e.target.value)}
              className="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5" placeholder="Nama gaya" />
            <label className="flex items-center gap-1 text-sm text-zinc-300">
              <input type="checkbox" checked={favorite} onChange={e => setFavorite(e.target.checked)} /> Favorit
            </label>
          </div>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2}
            placeholder="Catatan: cocok untuk klip apa? (dibaca Gemini)"
            className="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm" />

          <Group title="Huruf">
            <Row label="Font">
              <select value={r.font.family} onChange={e => {
                const f = opts.fonts.find(x => x.family === e.target.value)
                upd("font", { family: e.target.value, weight: f?.weights.includes(r.font.weight) ? r.font.weight : (f?.weights.slice(-1)[0] ?? "400") })
              }} className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm max-w-[200px]">
                {!fontInfo && <option value={r.font.family}>{r.font.family}</option>}
                {opts.fonts.map(f => <option key={f.family} value={f.family}>{f.family}</option>)}
              </select>
              <Select value={r.font.weight} options={weights} onChange={v => upd("font.weight", v)} />
            </Row>
            <Row label="Ukuran"><Slider value={r.size} min={60} max={170} step={1} onChange={v => upd("size", v)} /></Row>
            <Row label="Huruf"><Select value={r.case} options={opts.cases} onChange={v => upd("case", v)} /></Row>
            <Row label="Jarak huruf"><Slider value={r.letter_spacing} min={-4} max={20} step={1} onChange={v => upd("letter_spacing", v)} /></Row>
            <Row label="Kata per layar"><Slider value={r.words_per_line} min={1} max={5} step={1} onChange={v => upd("words_per_line", v)} /></Row>
            <Row label="Posisi"><Select value={r.position} options={opts.positions} onChange={v => upd("position", v)} /></Row>
          </Group>

          <Group title="Warna">
            <Row label="Mode"><Select value={r.color_mode} options={opts.color_modes} onChange={v => upd("color_mode", v)} /></Row>
            <Row label="Warna isi">
              {r.fill_colors.map((c, i) => (
                <span key={i} className="flex items-center">
                  <Color value={c} onChange={v => upd("fill_colors", r.fill_colors.map((x, j) => j === i ? v : x))} />
                  {r.fill_colors.length > 1 && (
                    <button className="text-zinc-500 hover:text-red-400 text-xs px-1"
                      onClick={() => upd("fill_colors", r.fill_colors.filter((_, j) => j !== i))}>×</button>
                  )}
                </span>
              ))}
              {r.fill_colors.length < 6 && (
                <button className="text-xs text-brand-400" onClick={() => upd("fill_colors", [...r.fill_colors, "#FFFFFF"])}>+ warna</button>
              )}
            </Row>
            <Row label="Kata highlight">
              <Color value={r.highlight.color} onChange={v => upd("highlight.color", v)} />
              <Slider value={r.highlight.scale} min={1} max={1.4} step={0.01} onChange={v => upd("highlight.scale", v)} fmt={v => `${Math.round(v * 100)}%`} />
            </Row>
          </Group>

          <Group title="Bingkai">
            <Row label="Outline">
              <Color value={r.outline.color} onChange={v => upd("outline.color", v)} />
              <Slider value={r.outline.width} min={0} max={0.35} step={0.01} onChange={v => upd("outline.width", v)} />
            </Row>
            <Row label="Bayangan">
              <Color value={r.shadow.color} onChange={v => upd("shadow.color", v)} />
              <Slider value={r.shadow.distance} min={0} max={0.12} step={0.005} onChange={v => upd("shadow.distance", v)} />
            </Row>
            <Row label="Gelap bayangan"><Slider value={r.shadow.opacity} min={0} max={1} step={0.05} onChange={v => upd("shadow.opacity", v)} /></Row>
            <Row label="Kotak latar">
              <input type="checkbox" checked={r.box.enabled} onChange={e => upd("box.enabled", e.target.checked)} />
              {r.box.enabled && <Color value={r.box.color} onChange={v => upd("box.color", v)} />}
            </Row>
            {r.box.enabled && <>
              <Row label="Transparansi kotak"><Slider value={r.box.opacity} min={0.2} max={1} step={0.05} onChange={v => upd("box.opacity", v)} /></Row>
              <Row label="Padding kotak"><Slider value={r.box.padding} min={0.05} max={0.4} step={0.01} onChange={v => upd("box.padding", v)} /></Row>
            </>}
          </Group>

          <Group title="Animasi">
            <Row label="Masuk"><Select value={r.animation.type} options={opts.anim_types} onChange={v => upd("animation.type", v)} /></Row>
            <Row label="Mulai dari"><Slider value={r.animation.from_scale} min={0} max={100} step={5} onChange={v => upd("animation.from_scale", v)} fmt={v => `${v}%`} /></Row>
            <Row label="Melewati"><Slider value={r.animation.overshoot} min={100} max={140} step={1} onChange={v => upd("animation.overshoot", v)} fmt={v => `${v}%`} /></Row>
            <Row label="Durasi"><Slider value={r.animation.duration_ms} min={60} max={450} step={10} onChange={v => upd("animation.duration_ms", v)} fmt={v => `${v}ms`} /></Row>
            {r.words_per_line > 1 && (
              <Row label="Kata aktif">
                <Select value={r.active_word.mode} options={opts.active_modes} onChange={v => upd("active_word.mode", v)} />
                {r.active_word.mode !== "none" && <Color value={r.active_word.color} onChange={v => upd("active_word.color", v)} />}
              </Row>
            )}
          </Group>

          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={() => save(false)} disabled={busy !== ""}>
              <Save size={14} /> {editingId ? "Simpan" : "Simpan gaya"}
            </button>
            {editingId && <button className="btn-primary bg-zinc-700" onClick={() => save(true)} disabled={busy !== ""}>
              <Copy size={14} /> Simpan sebagai baru
            </button>}
            {editingId && <button className="text-sm text-zinc-500 hover:text-red-400 flex items-center gap-1 ml-auto" onClick={remove}>
              <Trash2 size={14} /> Hapus
            </button>}
          </div>
        </div>

        {/* Preview + Gemini */}
        <div className="space-y-4">
          <div className="card space-y-2">
            <Row label="Klip preview">
              <select value={clipId ?? ""} onChange={e => setClipId(Number(e.target.value))}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs max-w-[200px]">
                {clips.map(c => <option key={c.candidate_id} value={c.candidate_id}>{c.title ?? `#${c.candidate_id}`}</option>)}
              </select>
            </Row>
            <button className="btn-primary w-full justify-center" onClick={preview} disabled={busy !== "" || !clips.length}>
              {busy === "preview" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Preview
            </button>
            {!clips.length && <p className="text-xs text-zinc-500">Jalankan Auto Edit sekali pada satu klip untuk mengaktifkan preview.</p>}
            {previewUrl && (
              <video key={previewUrl} src={previewUrl} autoPlay loop muted controls playsInline
                className="w-full rounded-lg bg-black aspect-[9/16]" />
            )}
          </div>

          <div className="card space-y-2">
            <h3 className="text-xs uppercase tracking-wide text-zinc-500 flex items-center gap-1"><Sparkles size={12} /> Buat dengan Gemini</h3>
            <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={3}
              placeholder='mis. "satu kata, warna-warni, outline hitam tebal, pop seperti CapCut"'
              className="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm" />
            <button className="btn-primary w-full justify-center" onClick={generate} disabled={busy !== "" || !desc.trim()}>
              {busy === "gen" ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {editingId ? "Ubah gaya ini" : "Buat resep"}
            </button>
            <p className="text-xs text-zinc-500">1 panggilan Gemini. Hasil dimuat ke editor, tidak langsung disimpan.</p>
          </div>
          {msg && <p className="text-xs text-brand-400">{msg}</p>}
        </div>
      </div>
    </div>
  )
}
