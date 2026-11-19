import { useState, useRef, useEffect } from "react"
import { Crop, Loader2, Download, ChevronDown, ChevronUp, Users } from "lucide-react"
import { api } from "../../lib/api"

// ---------------------------------------------------------------- types

interface CropPanel {
  person_id: number
  src_x: number; src_y: number
  src_w: number; src_h: number
  out_w: number; out_h: number
}

interface CropResult {
  strategy: string
  people_count: number
  confidence: number
  video_w: number
  video_h: number
  ffmpeg_filter: string
  panels: CropPanel[]
}

interface TrackStatus {
  status: "running" | "done" | "error"
  progress?: number
  output_path?: string
  strategy?: string
  people_count?: number
  frames_processed?: number
  duration_s?: number
  error?: string
}

// ---------------------------------------------------------------- strategy diagram

const STRATEGY_LABEL: Record<string, string> = {
  single:  "Single — 1 orang",
  grid_h2: "Split Horizontal — 2 orang",
  grid_v2: "Split Vertikal — 2 orang",
  grid_h3: "3 Panel — 3 orang",
  wide:    "Wide — center crop",
}

function StrategyDiagram({ strategy }: { strategy: string }) {
  const cell = "bg-brand-600/30 border border-brand-500/40 rounded"

  if (strategy === "grid_h2") return (
    <div className="flex gap-1 h-16 w-20">
      <div className={`flex-1 ${cell}`} />
      <div className={`flex-1 ${cell}`} />
    </div>
  )
  if (strategy === "grid_v2") return (
    <div className="flex flex-col gap-1 h-16 w-10">
      <div className={`flex-1 ${cell}`} />
      <div className={`flex-1 ${cell}`} />
    </div>
  )
  if (strategy === "grid_h3") return (
    <div className="flex gap-1 h-16 w-24">
      <div className={`flex-1 ${cell}`} />
      <div className={`flex-1 ${cell}`} />
      <div className={`flex-1 ${cell}`} />
    </div>
  )
  // single / wide
  return <div className={`h-16 w-10 ${cell}`} />
}

// ---------------------------------------------------------------- main

interface Props {
  jobId: string | number
  candidateId?: string | number
}

export function SmartCropPanel({ jobId, candidateId }: Props) {
  const [open, setOpen]           = useState(false)
  const [cropStatus, setCropStatus] = useState<"idle" | "running" | "done" | "error">("idle")
  const [cropResult, setCropResult] = useState<CropResult | null>(null)
  const [cropErr, setCropErr]     = useState("")

  const [trackStatus, setTrackStatus] = useState<"idle" | "running" | "done" | "error">("idle")
  const [trackInfo, setTrackInfo]   = useState<TrackStatus | null>(null)
  const [trackId, setTrackId]       = useState<string | null>(null)
  const [trackErr, setTrackErr]     = useState("")

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stopPoll = () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null }
  useEffect(() => () => stopPoll(), [])

  // ---- crop analysis (sync, cepat)
  const analyzeCrop = async () => {
    setCropStatus("running")
    setCropErr("")
    try {
      const { data } = await api.post("/api/analyzer/crop", {
        job_id: Number(jobId),
        candidate_id: candidateId ? Number(candidateId) : undefined,
        target_w: 1080,
        target_h: 1920,
      })
      setCropResult(data as CropResult)
      setCropStatus("done")
      setOpen(true)
    } catch (e: any) {
      setCropErr(e?.response?.data?.detail ?? "Gagal analisis crop.")
      setCropStatus("error")
    }
  }

  // ---- track-crop (async, bisa lama)
  const startTrackCrop = async () => {
    if (!cropResult) return
    setTrackStatus("running")
    setTrackErr("")
    setTrackInfo(null)
    try {
      const { data } = await api.post("/api/analyzer/track-crop", {
        job_id: Number(jobId),
        candidate_id: candidateId ? Number(candidateId) : undefined,
        target_w: 1080,
        target_h: 1920,
        detect_interval: 15,
      })
      const tid = data.track_id as string
      setTrackId(tid)
      pollRef.current = setInterval(async () => {
        try {
          const { data: st } = await api.get(`/api/analyzer/track-crop/${tid}/status`)
          setTrackInfo(st as TrackStatus)
          if (st.status === "done") {
            setTrackStatus("done")
            stopPoll()
          } else if (st.status === "error") {
            setTrackErr(st.error ?? "Tracking gagal.")
            setTrackStatus("error")
            stopPoll()
          }
        } catch { /* retry */ }
      }, 2000)
    } catch (e: any) {
      setTrackErr(e?.response?.data?.detail ?? "Gagal memulai tracking.")
      setTrackStatus("error")
    }
  }

  const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

  return (
    <div className="card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Crop size={14} />
          Smart Crop
          {cropResult && (
            <span className="normal-case text-zinc-500 font-normal">
              — {STRATEGY_LABEL[cropResult.strategy] ?? cropResult.strategy}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-2">
          {cropStatus === "idle" && (
            <button
              onClick={analyzeCrop}
              className="text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
            >
              Analyze
            </button>
          )}
          {cropStatus === "running" && (
            <span className="flex items-center gap-1.5 text-xs text-zinc-500">
              <Loader2 size={12} className="animate-spin" /> Analyzing…
            </span>
          )}
          {cropStatus === "error" && (
            <span className="text-xs text-red-400">{cropErr}</span>
          )}
          {cropResult && (
            <button onClick={() => setOpen(o => !o)} className="text-zinc-500 hover:text-zinc-300 transition-colors">
              {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          )}
        </div>
      </div>

      {/* Detail */}
      {open && cropResult && (
        <div className="space-y-4 pt-1 border-t border-zinc-800">
          {/* Strategy card */}
          <div className="flex items-center gap-4 p-3 rounded-lg bg-zinc-900 border border-zinc-800">
            <StrategyDiagram strategy={cropResult.strategy} />
            <div className="space-y-1">
              <p className="text-sm text-zinc-200 font-medium">
                {STRATEGY_LABEL[cropResult.strategy] ?? cropResult.strategy}
              </p>
              <p className="text-xs text-zinc-500 flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <Users size={11} />
                  {cropResult.people_count} orang terdeteksi
                </span>
                <span>Confidence: {(cropResult.confidence * 100).toFixed(0)}%</span>
                <span>Source: {cropResult.video_w}×{cropResult.video_h}</span>
              </p>
              {cropResult.panels.length > 1 && (
                <p className="text-xs text-zinc-600">
                  Output: {cropResult.panels.map(p => `${p.out_w}×${p.out_h}`).join(" + ")}
                </p>
              )}
            </div>
          </div>

          {/* FFmpeg filter preview */}
          <details className="group">
            <summary className="text-xs text-zinc-600 cursor-pointer hover:text-zinc-400 transition-colors list-none flex items-center gap-1">
              <span className="group-open:rotate-90 inline-block transition-transform">▶</span>
              FFmpeg filter
            </summary>
            <pre className="mt-1 text-[10px] text-zinc-500 bg-zinc-900 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
              {cropResult.ffmpeg_filter}
            </pre>
          </details>

          {/* Track-crop section */}
          <div className="border-t border-zinc-800 pt-3 space-y-2">
            <p className="text-xs text-zinc-500">
              <span className="text-zinc-300">Dynamic Tracking</span>
              {" "}— crop window mengikuti wajah setiap frame
            </p>

            {trackStatus === "idle" && (
              <button
                onClick={startTrackCrop}
                className="text-xs px-3 py-1.5 rounded-lg border border-zinc-700 hover:border-zinc-500 text-zinc-300 hover:text-white transition-colors"
              >
                Start Face Tracking
              </button>
            )}

            {trackStatus === "running" && trackInfo && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs text-zinc-500">
                  <span className="flex items-center gap-1.5">
                    <Loader2 size={11} className="animate-spin" />
                    Tracking… {trackInfo.progress ?? 0}%
                  </span>
                  <span className="text-zinc-600">
                    {STRATEGY_LABEL[trackInfo.strategy ?? ""] ?? trackInfo.strategy ?? ""}
                  </span>
                </div>
                <div className="h-1.5 w-full bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-brand-500 rounded-full transition-all duration-500"
                    style={{ width: `${trackInfo.progress ?? 0}%` }}
                  />
                </div>
              </div>
            )}

            {trackStatus === "error" && (
              <p className="text-xs text-red-400">{trackErr}</p>
            )}

            {trackStatus === "done" && trackId && trackInfo && (
              <div className="flex items-center gap-3 p-2 rounded-lg bg-green-900/20 border border-green-800/40">
                <div className="flex-1 text-xs text-zinc-400 space-y-0.5">
                  <p className="text-green-400 font-medium">Selesai</p>
                  <p>
                    {trackInfo.frames_processed?.toLocaleString()} frame
                    · {trackInfo.duration_s}s
                    · {STRATEGY_LABEL[trackInfo.strategy ?? ""] ?? trackInfo.strategy}
                  </p>
                </div>
                <a
                  href={`${API_BASE}/api/analyzer/track-crop/${trackId}/file`}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
                  download
                >
                  <Download size={12} /> Download
                </a>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
