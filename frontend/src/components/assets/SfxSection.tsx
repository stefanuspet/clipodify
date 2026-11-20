import { useEffect, useRef, useState } from "react"
import { Music2, Pause, Play, X } from "lucide-react"
import { api } from "../../lib/api"

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

export type SfxItem = {
  id: string; name: string; filename: string
  category: string; duration_s: number; size_kb: number; tag?: string
}

const SFX_CAT_BADGE: Record<string, string> = {
  transition: "bg-blue-500/20 text-blue-300",
  emphasis:   "bg-yellow-500/20 text-yellow-300",
  impact:     "bg-red-500/20 text-red-300",
  tone:       "bg-teal-500/20 text-teal-300",
  buildup:    "bg-purple-500/20 text-purple-300",
  effect:     "bg-emerald-500/20 text-emerald-300",
  other:      "bg-zinc-500/20 text-zinc-400",
}

export function SfxSection({ refreshKey = 0 }: { refreshKey?: number }) {
  const [sfxList, setSfxList]     = useState<SfxItem[]>([])
  const [playing, setPlaying]     = useState<string | null>(null)
  const audioRef                  = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    api.get("/api/editor/sfx").then(r => setSfxList(r.data))
  }, [refreshKey])

  function togglePlay(sfx: SfxItem) {
    const url = `${API}/assets/sfx/${sfx.filename}`
    if (playing === sfx.id) {
      audioRef.current?.pause()
      setPlaying(null)
      return
    }
    if (audioRef.current) audioRef.current.pause()
    const audio = new Audio(url)
    audio.onended = () => setPlaying(null)
    audio.play().catch(() => {})
    audioRef.current = audio
    setPlaying(sfx.id)
  }

  async function handleDelete(id: string) {
    await api.delete(`/api/editor/sfx/${id}`)
    setSfxList(prev => prev.filter(s => s.id !== id))
    if (playing === id) { audioRef.current?.pause(); setPlaying(null) }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-zinc-300 flex items-center gap-2">
          <Music2 size={16} className="text-brand-500" />
          Sound Effects
          <span className="badge bg-zinc-800 text-zinc-400">{sfxList.length}</span>
        </h2>
        <span className="text-xs text-zinc-500">Tambah lewat "Impor Audio" (lisensi wajib)</span>
      </div>

      <div className="space-y-1.5">
        {sfxList.map(sfx => (
          <div
            key={sfx.id}
            className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-zinc-900 border border-zinc-800 hover:border-zinc-700 transition-colors"
          >
            <button
              onClick={() => togglePlay(sfx)}
              className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 transition-colors
                ${playing === sfx.id
                  ? "bg-brand-600 text-white"
                  : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700 hover:text-white"
                }`}
            >
              {playing === sfx.id ? <Pause size={14} /> : <Play size={14} />}
            </button>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-100">{sfx.name}</span>
                <span className={`badge text-[9px] ${SFX_CAT_BADGE[sfx.category] ?? SFX_CAT_BADGE.other}`}>
                  {sfx.tag ?? sfx.category}
                </span>
              </div>
              <p className="text-zinc-500 text-xs">{sfx.duration_s}s · {sfx.size_kb} KB · {sfx.filename}</p>
            </div>

            <div className="hidden sm:flex items-center gap-0.5 h-6 w-20">
              {Array.from({ length: 20 }).map((_, i) => (
                <div
                  key={i}
                  className={`w-1 rounded-full transition-colors ${playing === sfx.id ? "bg-brand-500/70" : "bg-zinc-700"}`}
                  style={{ height: `${Math.max(20, Math.sin(i * 0.8) * 50 + 50)}%` }}
                />
              ))}
            </div>

            {!sfx.id.startsWith("sfx_") && (
              <button
                onClick={() => handleDelete(sfx.id)}
                className="text-zinc-600 hover:text-red-400 transition-colors"
              >
                <X size={14} />
              </button>
            )}
          </div>
        ))}

        {sfxList.length === 0 && (
          <div className="text-center py-8 text-zinc-600 text-sm">
            Belum ada SFX. Upload file MP3/WAV kamu.
          </div>
        )}
      </div>
    </section>
  )
}
