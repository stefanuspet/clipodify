import { useEffect, useRef, useState } from "react"
import { Layers, RefreshCw, Loader2, CheckCircle2, Play } from "lucide-react"
import { api } from "../lib/api"
import { PreviewModal } from "../components/assets/PreviewModal"
import { FilterSection } from "../components/assets/FilterSection"
import { SfxSection } from "../components/assets/SfxSection"

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

type SubPreset = {
  id: string; label: string; font: string; font_group: string
  size: number; color: string; animation: string; highlight_color?: string
}

const ANIM_BADGE: Record<string, string> = {
  word_pop:        "bg-yellow-500/20 text-yellow-300",
  word_pop_size:   "bg-amber-500/20 text-amber-300",
  word_bounce:     "bg-orange-500/20 text-orange-300",
  word_slide:      "bg-lime-500/20 text-lime-300",
  word_fade:       "bg-blue-500/20 text-blue-300",
  karaoke_sweep:   "bg-purple-500/20 text-purple-300",
  karaoke_bold:    "bg-violet-500/20 text-violet-300",
  karaoke_chip:    "bg-indigo-500/20 text-indigo-300",
  scale_highlight: "bg-red-500/20 text-red-300",
  sentence_pop:    "bg-orange-500/20 text-orange-300",
  slide_up:        "bg-green-500/20 text-green-300",
  dark_box:        "bg-zinc-500/20 text-zinc-300",
  zoom_in:         "bg-cyan-500/20 text-cyan-300",
  typewriter:      "bg-teal-500/20 text-teal-300",
  neon_glow:       "bg-emerald-500/20 text-emerald-300",
  none:            "bg-zinc-700/40 text-zinc-400",
}

const FONT_ORDER = [
  "anton",
  "montserrat_xbold",
  "montserrat_bold",
  "bebas_neue",
  "poppins_bold",
  "poppins_xbold",
]

const FONT_LABELS: Record<string, string> = {
  anton:            "Anton",
  montserrat_xbold: "Montserrat ExtraBold",
  montserrat_bold:  "Montserrat Bold",
  bebas_neue:       "Bebas Neue",
  poppins_bold:     "Poppins Bold",
  poppins_xbold:    "Poppins ExtraBold",
}

function PresetCard({
  preset,
  selected,
  onSelect,
  onPreview,
}: {
  preset: SubPreset
  selected: boolean
  onSelect: (id: string) => void
  onPreview: (preset: SubPreset) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [loaded, setLoaded]   = useState(false)
  const [hovered, setHovered] = useState(false)

  const previewUrl = `${API}/assets/previews/subtitle_${preset.id}.mp4`

  function handleEnter() {
    setHovered(true)
    videoRef.current?.play().catch(() => {})
  }

  function handleLeave() {
    setHovered(false)
    const v = videoRef.current
    if (v) { v.pause(); v.currentTime = 0 }
  }

  return (
    <div
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      onClick={() => onSelect(preset.id)}
      className={`relative cursor-pointer rounded-xl overflow-hidden border-2 transition-all duration-200
        ${selected
          ? "border-brand-500 shadow-lg shadow-brand-500/20"
          : "border-zinc-800 hover:border-zinc-600"
        }`}
    >
      <div className="relative bg-zinc-950 aspect-[9/16]">
        <div
          className={`absolute inset-0 flex items-center justify-center transition-opacity duration-300
            ${hovered ? "opacity-0" : "opacity-100"}`}
        >
          <div className="text-center space-y-1.5 px-2">
            <p
              className="font-bold leading-tight"
              style={{
                fontFamily: preset.font.split(" ")[0],
                color: preset.highlight_color ?? preset.color,
                fontSize: "clamp(13px, 3.5vw, 20px)",
                textShadow: "0 0 8px rgba(0,0,0,0.9), 0 2px 4px rgba(0,0,0,1)",
              }}
            >
              {preset.label}
            </p>
            <p className="text-zinc-600 text-[10px]">hover preview</p>
          </div>
        </div>

        {!loaded && hovered && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 size={20} className="animate-spin text-zinc-500" />
          </div>
        )}

        <video
          ref={videoRef}
          src={previewUrl}
          muted loop playsInline preload="none"
          onCanPlay={() => setLoaded(true)}
          className={`w-full h-full object-cover transition-opacity duration-300
            ${hovered && loaded ? "opacity-100" : "opacity-0"}`}
        />

        <button
          onClick={e => { e.stopPropagation(); onPreview(preset) }}
          className={`absolute top-2 right-2 w-7 h-7 bg-black/60 hover:bg-black/90 rounded-lg
            flex items-center justify-center text-white transition-all duration-150
            ${hovered ? "opacity-100 scale-100" : "opacity-0 scale-90"}`}
          title="Preview besar"
        >
          <Play size={12} />
        </button>
      </div>

      <div className="bg-zinc-900 px-2.5 py-1.5 space-y-1">
        <div className="flex items-center justify-between gap-1">
          <span className="text-xs font-medium text-zinc-100 truncate">{preset.label}</span>
          {selected && <CheckCircle2 size={12} className="text-brand-400 shrink-0" />}
        </div>
        <span className={`inline-block badge text-[9px] ${ANIM_BADGE[preset.animation] ?? ANIM_BADGE.none}`}>
          {preset.animation}
        </span>
      </div>
    </div>
  )
}

export function AssetsPage() {
  const [presets, setPresets]             = useState<SubPreset[]>([])
  const [selected, setSelected]           = useState<string | null>(null)
  const [generating, setGenerating]       = useState(false)
  const [activeFont, setActiveFont]       = useState<string>("anton")
  const [previewPreset, setPreviewPreset] = useState<SubPreset | null>(null)

  useEffect(() => {
    api.get("/api/editor/presets/subtitle").then(r => setPresets(r.data))
  }, [])

  async function regenAll() {
    setGenerating(true)
    try {
      await api.post("/api/editor/presets/subtitle/preview-all")
      await new Promise(r => setTimeout(r, 8000))
      setPresets(prev => [...prev])
    } finally {
      setGenerating(false)
    }
  }

  const byFont = FONT_ORDER.reduce<Record<string, SubPreset[]>>((acc, key) => {
    acc[key] = presets.filter(p => p.font_group === key)
    return acc
  }, {})

  const visiblePresets = byFont[activeFont] ?? []
  const selectedPreset = presets.find(p => p.id === selected)

  return (
    <div className="max-w-6xl mx-auto py-8 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Layers size={22} className="text-brand-500" /> Asset Browser
          </h1>
          <p className="text-zinc-400 text-sm mt-0.5">
            {presets.length} preset · 6 font · 15 animasi · hover untuk preview
          </p>
        </div>
        <button className="btn-ghost text-xs" onClick={regenAll} disabled={generating}>
          {generating
            ? <><Loader2 size={13} className="animate-spin" /> Generating…</>
            : <><RefreshCw size={13} /> Regen Preview</>
          }
        </button>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {FONT_ORDER.map(key => (
          <button
            key={key}
            onClick={() => setActiveFont(key)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors
              ${activeFont === key
                ? "bg-brand-600/30 text-brand-300 border border-brand-600/40"
                : "bg-zinc-800/60 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 border border-transparent"
              }`}
          >
            {FONT_LABELS[key]}
            <span className="ml-1.5 text-[10px] opacity-60">{byFont[key]?.length ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
        {visiblePresets.map(p => (
          <PresetCard
            key={p.id}
            preset={p}
            selected={selected === p.id}
            onSelect={setSelected}
            onPreview={setPreviewPreset}
          />
        ))}
        {visiblePresets.length === 0 && (
          <div className="col-span-full text-center py-12 text-zinc-600 text-sm">
            Loading presets…
          </div>
        )}
      </div>

      {selectedPreset && (
        <section className="card space-y-3">
          <h3 className="font-semibold">
            {FONT_LABELS[selectedPreset.font_group]} · {selectedPreset.label}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div><p className="label">Font</p><p className="text-zinc-200">{selectedPreset.font}</p></div>
            <div><p className="label">Size</p><p className="text-zinc-200">{selectedPreset.size}px</p></div>
            <div><p className="label">Animasi</p><p className="text-zinc-200">{selectedPreset.animation}</p></div>
            <div>
              <p className="label">Warna</p>
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full border border-zinc-600" style={{ background: selectedPreset.color }} />
                <span className="text-zinc-200">{selectedPreset.color}</span>
              </div>
            </div>
            {selectedPreset.highlight_color && (
              <div>
                <p className="label">Highlight</p>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 rounded-full border border-zinc-600" style={{ background: selectedPreset.highlight_color }} />
                  <span className="text-zinc-200">{selectedPreset.highlight_color}</span>
                </div>
              </div>
            )}
          </div>
          <p className="text-zinc-500 text-xs">
            Preset ID: <code className="bg-zinc-800 px-1 rounded">{selectedPreset.id}</code>
          </p>
        </section>
      )}

      <FilterSection />
      <SfxSection />

      {previewPreset && (
        <PreviewModal
          preset={previewPreset}
          onClose={() => setPreviewPreset(null)}
        />
      )}
    </div>
  )
}
