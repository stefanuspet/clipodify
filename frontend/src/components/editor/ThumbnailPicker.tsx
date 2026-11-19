import { useState, useRef, useEffect } from "react"
import { Image, Loader2, ChevronDown, ChevronUp, Download, CheckCircle2 } from "lucide-react"
import { api } from "../../lib/api"

// ---------------------------------------------------------------- types

interface ThumbCandidate {
  time_s: number
  filename: string
  face_count: number
  face_confidence: number
  emotion: string
  energy: number
  score: number
  person?: string
  reasons?: Record<string, number>
}

// ---------------------------------------------------------------- constants

const EMOTION_EMOJI: Record<string, string> = {
  laughing: "😂", excited: "🤩", angry: "😡",
  sad: "😢", shocked: "😱", neutral: "😐", silence: "🔇",
}

function fmt(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, "0")}`
}

// ---------------------------------------------------------------- component

interface Props {
  jobId:        string | number
  candidateId?: string | number
  onSeek?:      (seconds: number) => void
}

export function ThumbnailPicker({ jobId, candidateId, onSeek }: Props) {
  const [open, setOpen]       = useState(false)
  const [status, setStatus]   = useState<"idle" | "running" | "done" | "error">("idle")
  const [frames, setFrames]   = useState<ThumbCandidate[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [errMsg, setErrMsg]   = useState("")
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [cacheKey, setCacheKey] = useState<string>("")

  const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"
  const stopPoll = () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null }
  useEffect(() => () => stopPoll(), [])

  const startExtraction = async () => {
    setStatus("running")
    setErrMsg("")

    let key = ""
    try {
      const { data } = await api.post("/api/analyzer/thumbnail", {
        job_id:       Number(jobId),
        candidate_id: candidateId ? Number(candidateId) : undefined,
        n: 12,
      })
      // poll URL contains the cache_key: "/api/analyzer/thumbnail/{key}/result"
      key = (data.poll as string).split("/")[4] ?? String(jobId)
      setCacheKey(key)
      if (data.status === "cached") {
        setFrames(data.result as ThumbCandidate[])
        setStatus("done"); setOpen(true); return
      }
    } catch (err: any) {
      setErrMsg(err?.response?.data?.detail ?? "Gagal memulai ekstraksi.")
      setStatus("error"); return
    }

    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/analyzer/thumbnail/${key}/result`)
        if (data.status === "done") {
          setFrames(data.result as ThumbCandidate[])
          setStatus("done")
          setOpen(true)
          stopPoll()
        } else if (data.status === "error") {
          setErrMsg((data.error as string) ?? "Gagal ekstrak thumbnail.")
          setStatus("error")
          stopPoll()
        }
      } catch { /* retry */ }
    }, 1500)
  }

  const downloadSelected = () => {
    if (!selected || !cacheKey) return
    const link = document.createElement("a")
    link.href = `${API_BASE}/api/analyzer/thumbnail/${cacheKey}/frame/${selected}`
    link.download = selected
    link.click()
  }

  return (
    <div className="card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Image size={14} />
          Thumbnail Picker
          {status === "done" && (
            <span className="normal-case text-zinc-500 font-normal">
              — {frames.length} frame kandidat
            </span>
          )}
        </h2>
        <div className="flex items-center gap-2">
          {selected && status === "done" && (
            <button
              onClick={downloadSelected}
              className="flex items-center gap-1.5 text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
            >
              <Download size={11} /> Download
            </button>
          )}
          {status === "idle" && (
            <button
              onClick={startExtraction}
              className="text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
            >
              Generate
            </button>
          )}
          {status === "running" && (
            <span className="flex items-center gap-1.5 text-xs text-zinc-500">
              <Loader2 size={12} className="animate-spin" /> Generating…
            </span>
          )}
          {status === "error" && (
            <span className="text-xs text-red-400">{errMsg}</span>
          )}
          {status === "done" && (
            <button onClick={() => setOpen(o => !o)} className="text-zinc-500 hover:text-zinc-300 transition-colors">
              {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          )}
        </div>
      </div>

      {/* Grid */}
      {open && frames.length > 0 && (
        <div className="space-y-3">
          {/* Selected info bar */}
          {selected && (() => {
            const f = frames.find(x => x.filename === selected)
            return f ? (
              <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg bg-brand-600/10 border border-brand-500/30">
                <CheckCircle2 size={13} className="text-brand-400" />
                <span className="text-zinc-300">
                  Dipilih: {fmt(f.time_s)}
                  {f.person && <>{" · "}{f.person}</>}
                  {" · "}{EMOTION_EMOJI[f.emotion]} {f.emotion}
                  {" · "} score {(f.score * 100).toFixed(0)}
                  {f.reasons && <span className="text-zinc-500">
                    {" · "}tajam {(f.reasons.sharp * 100).toFixed(0)} · mata {(f.reasons.eyes * 100).toFixed(0)}
                    {" · "}hadap kamera {(f.reasons.frontal * 100).toFixed(0)}
                  </span>}
                </span>
              </div>
            ) : null
          })()}

          {/* Thumbnail grid */}
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            {frames.map(frame => {
              const isSelected = selected === frame.filename
              const imgUrl = `${API_BASE}/api/analyzer/thumbnail/${cacheKey}/frame/${frame.filename}`
              return (
                <button
                  key={frame.filename}
                  onClick={() => {
                    setSelected(isSelected ? null : frame.filename)
                    onSeek?.(frame.time_s)
                  }}
                  className={`relative group rounded-lg overflow-hidden border-2 transition-all aspect-[9/16]
                    ${isSelected
                      ? "border-brand-500 shadow-lg shadow-brand-500/20"
                      : "border-zinc-700 hover:border-zinc-500"
                    }`}
                >
                  {/* Frame image */}
                  <img
                    src={imgUrl}
                    alt={`${fmt(frame.time_s)}`}
                    className="w-full h-full object-cover"
                    loading="lazy"
                  />

                  {/* Score badge (top-right) */}
                  <span className="absolute top-1 right-1 text-[10px] px-1 py-0.5 rounded bg-black/70 text-zinc-200">
                    {(frame.score * 100).toFixed(0)}
                  </span>

                  {/* Bottom overlay */}
                  <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 pb-1.5 pt-4">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-zinc-300 font-mono">{fmt(frame.time_s)}</span>
                      <span className="text-[11px]">{EMOTION_EMOJI[frame.emotion] ?? ""}</span>
                    </div>
                    {frame.face_count > 0 && (
                      <div className="text-[9px] text-zinc-500 mt-0.5">
                        {frame.face_count} wajah · {(frame.face_confidence * 100).toFixed(0)}%
                      </div>
                    )}
                  </div>

                  {/* Selected check */}
                  {isSelected && (
                    <div className="absolute inset-0 bg-brand-500/10 flex items-start justify-start p-1">
                      <CheckCircle2 size={16} className="text-brand-400" />
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          <p className="text-xs text-zinc-600">
            Frame diurutkan dari skor tertinggi. Klik untuk pilih, klik lagi untuk batal.
          </p>
        </div>
      )}
    </div>
  )
}
