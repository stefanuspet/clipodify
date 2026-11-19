import { useEffect, useRef, useState } from "react"
import { CheckCircle2, Loader2, Sliders } from "lucide-react"
import { api } from "../../lib/api"

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

export type FilterPreset = {
  id: string; label: string
  contrast: number; saturation: number; brightness: number; gamma: number
}

const FILTER_BADGE: Record<string, string> = {
  none:      "bg-zinc-700/40 text-zinc-400",
  cinematic: "bg-slate-500/20 text-slate-300",
  warm:      "bg-amber-500/20 text-amber-300",
  cold:      "bg-blue-500/20 text-blue-300",
  viral:     "bg-fuchsia-500/20 text-fuchsia-300",
  moody:     "bg-zinc-500/20 text-zinc-400",
}

function FilterCard({
  preset, onSelect, selected,
}: { preset: FilterPreset; onSelect: (id: string) => void; selected: boolean }) {
  const [hovered, setHovered] = useState(false)
  const [loaded, setLoaded]   = useState(false)
  const videoRef              = useRef<HTMLVideoElement>(null)
  const previewUrl = `${API}/api/editor/presets/filter/${preset.id}/preview`

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
      <div className="relative aspect-[9/16] bg-zinc-950">
        <img
          src={`${API}/assets/samples/sample_portrait.jpg`}
          style={{
            filter: [
              `contrast(${preset.contrast})`,
              `saturate(${preset.saturation})`,
              `brightness(${1 + preset.brightness})`,
            ].join(" "),
          }}
          className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-300
            ${hovered ? "opacity-0" : "opacity-100"}`}
          loading="lazy"
          alt={preset.label}
        />

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
      </div>

      <div className="bg-zinc-900 px-2.5 py-1.5 space-y-1">
        <div className="flex items-center justify-between gap-1">
          <span className="text-xs font-medium text-zinc-100 truncate">{preset.label}</span>
          {selected && <CheckCircle2 size={12} className="text-brand-400 shrink-0" />}
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`badge text-[9px] ${FILTER_BADGE[preset.id] ?? FILTER_BADGE.none}`}>
            {preset.id}
          </span>
          <span className="text-zinc-600 text-[9px]">sat {preset.saturation.toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}

export function FilterSection() {
  const [filters, setFilters]   = useState<FilterPreset[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    api.get("/api/editor/presets/filter").then(r => {
      setFilters(r.data)
      api.post("/api/editor/presets/filter/preview-all").catch(() => {})
    })
  }, [])

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Sliders size={16} className="text-brand-500" />
        <h2 className="font-semibold text-zinc-300">Color Filter</h2>
        <span className="badge bg-zinc-800 text-zinc-400">{filters.length}</span>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
        {filters.map(f => (
          <FilterCard
            key={f.id}
            preset={f}
            selected={selected === f.id}
            onSelect={setSelected}
          />
        ))}
      </div>
    </section>
  )
}
