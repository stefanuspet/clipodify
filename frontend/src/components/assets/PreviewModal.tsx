import { useEffect, useRef } from "react"
import { X } from "lucide-react"

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

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

type SubPreset = {
  id: string; label: string; font: string; font_group: string
  size: number; color: string; animation: string; highlight_color?: string
}

export function PreviewModal({ preset, onClose }: { preset: SubPreset; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    videoRef.current?.play().catch(() => {})
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const previewUrl = `${API}/assets/previews/subtitle_${preset.id}.mp4`

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div className="relative flex gap-6 items-start" onClick={e => e.stopPropagation()}>
        <div className="relative" style={{ height: "85vh", aspectRatio: "9/16" }}>
          <video
            ref={videoRef}
            src={previewUrl}
            muted loop playsInline autoPlay
            className="w-full h-full object-cover rounded-2xl"
          />
          <div className="absolute bottom-0 inset-x-0 p-4 rounded-b-2xl bg-gradient-to-t from-black/80 to-transparent">
            <p className="text-white font-bold text-lg">{preset.label}</p>
            <p className="text-zinc-400 text-sm">{preset.font}</p>
            <span className={`mt-1 inline-block badge text-[10px] ${ANIM_BADGE[preset.animation] ?? ANIM_BADGE.none}`}>
              {preset.animation}
            </span>
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5 w-56 space-y-4 self-center">
          <div className="space-y-1">
            <p className="text-zinc-500 text-xs uppercase tracking-wider">Font</p>
            <p className="text-zinc-100 text-sm font-medium">{preset.font}</p>
          </div>
          <div className="space-y-1">
            <p className="text-zinc-500 text-xs uppercase tracking-wider">Size</p>
            <p className="text-zinc-100 text-sm">{preset.size}px</p>
          </div>
          <div className="space-y-1">
            <p className="text-zinc-500 text-xs uppercase tracking-wider">Animasi</p>
            <p className="text-zinc-100 text-sm">{preset.animation}</p>
          </div>
          <div className="space-y-1">
            <p className="text-zinc-500 text-xs uppercase tracking-wider">Warna</p>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded-full border border-zinc-600 shrink-0" style={{ background: preset.color }} />
              <span className="text-zinc-100 text-sm">{preset.color}</span>
            </div>
          </div>
          {preset.highlight_color && (
            <div className="space-y-1">
              <p className="text-zinc-500 text-xs uppercase tracking-wider">Highlight</p>
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full border border-zinc-600 shrink-0" style={{ background: preset.highlight_color }} />
                <span className="text-zinc-100 text-sm">{preset.highlight_color}</span>
              </div>
            </div>
          )}
          <div className="space-y-1">
            <p className="text-zinc-500 text-xs uppercase tracking-wider">Preset ID</p>
            <code className="text-[11px] bg-zinc-800 px-2 py-1 rounded text-zinc-300 break-all">{preset.id}</code>
          </div>
        </div>

        <button
          onClick={onClose}
          className="absolute -top-3 -right-3 w-8 h-8 bg-zinc-800 hover:bg-zinc-700 rounded-full flex items-center justify-center text-zinc-400 hover:text-white transition-colors"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  )
}
