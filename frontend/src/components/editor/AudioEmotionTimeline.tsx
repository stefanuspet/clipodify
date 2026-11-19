import { useEffect, useRef, useState } from "react"
import { Activity, ChevronDown, ChevronUp, Loader2 } from "lucide-react"
import { api } from "../../lib/api"

// ---------------------------------------------------------------- types

interface AudioWindow {
  start: number
  end: number
  emotion: string
  energy: number
  intensity: number
  pitch_hz: number
  speech_ratio: number
}

interface AudioSegment {
  start: number
  end: number
  emotion: string
  avg_energy: number
  intensity: number
  description: string
}

interface AudioSummary {
  dominant_emotion: string
  avg_energy: number
  peak_moments: { time: number; emotion: string; intensity: number }[]
  emotion_counts: Record<string, number>
}

interface AudioResult {
  duration_s: number
  windows: AudioWindow[]
  segments: AudioSegment[]
  summary: AudioSummary
}

// ---------------------------------------------------------------- constants

const EMOTION_COLORS: Record<string, string> = {
  laughing: "#22c55e",
  excited:  "#f97316",
  angry:    "#ef4444",
  sad:      "#3b82f6",
  neutral:  "#6b7280",
  silence:  "#1f2937",
}

const EMOTION_EMOJI: Record<string, string> = {
  laughing: "😂",
  excited:  "🤩",
  angry:    "😡",
  sad:      "😢",
  neutral:  "😐",
  silence:  "🔇",
}

const EMOTION_LABEL: Record<string, string> = {
  laughing: "Laughing",
  excited:  "Excited",
  angry:    "Angry",
  sad:      "Sad",
  neutral:  "Neutral",
  silence:  "Silence",
}

// ---------------------------------------------------------------- helpers

function fmt(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, "0")}`
}

function colorWithAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

// ---------------------------------------------------------------- sub-components

function EmotionBar({ windows, duration, onSeek }: {
  windows: AudioWindow[]
  duration: number
  onSeek?: (t: number) => void
}) {
  const [hovered, setHovered] = useState<{ w: AudioWindow; x: number } | null>(null)

  if (!windows.length || !duration) return null

  return (
    <div className="relative h-8 rounded overflow-hidden cursor-pointer select-none"
      onMouseLeave={() => setHovered(null)}
    >
      {/* Colored segment strip */}
      <div className="flex h-full w-full">
        {windows.map((w, i) => {
          const widthPct = ((w.end - w.start) / duration) * 100
          const color = EMOTION_COLORS[w.emotion] ?? "#6b7280"
          return (
            <div
              key={i}
              style={{
                width: `${widthPct}%`,
                background: colorWithAlpha(color, 0.25 + w.energy * 0.6),
                flexShrink: 0,
              }}
              onMouseEnter={e => setHovered({ w, x: (e.currentTarget.getBoundingClientRect().left + e.currentTarget.getBoundingClientRect().right) / 2 })}
              onClick={() => onSeek?.(w.start)}
            />
          )
        })}
      </div>

      {/* Tooltip */}
      {hovered && (
        <div
          className="absolute -top-10 z-10 pointer-events-none"
          style={{ left: `clamp(0px, calc(${(hovered.w.start / duration) * 100}% ), calc(100% - 120px))` }}
        >
          <div className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs whitespace-nowrap shadow-lg">
            <span className="mr-1">{EMOTION_EMOJI[hovered.w.emotion]}</span>
            <span className="text-zinc-200">{EMOTION_LABEL[hovered.w.emotion]}</span>
            <span className="text-zinc-500 ml-2">{fmt(hovered.w.start)} · energy {(hovered.w.energy * 100).toFixed(0)}%</span>
          </div>
        </div>
      )}
    </div>
  )
}

function EnergyBars({ windows, duration, onSeek }: {
  windows: AudioWindow[]
  duration: number
  onSeek?: (t: number) => void
}) {
  if (!windows.length || !duration) return null

  return (
    <div className="flex items-end h-12 gap-px cursor-pointer"
      onClick={e => {
        const rect = e.currentTarget.getBoundingClientRect()
        const pct = (e.clientX - rect.left) / rect.width
        onSeek?.(pct * duration)
      }}
    >
      {windows.map((w, i) => {
        const color = EMOTION_COLORS[w.emotion] ?? "#6b7280"
        const height = w.emotion === "silence" ? 4 : Math.max(4, w.energy * 48)
        return (
          <div
            key={i}
            style={{
              height: `${height}px`,
              flex: 1,
              background: colorWithAlpha(color, 0.5 + w.intensity * 0.4),
              minWidth: 1,
            }}
          />
        )
      })}
    </div>
  )
}

function PeakMarkers({ peaks, duration, onSeek }: {
  peaks: { time: number; emotion: string; intensity: number }[]
  duration: number
  onSeek?: (t: number) => void
}) {
  if (!peaks.length || !duration) return null

  return (
    <div className="relative h-6">
      {peaks.map((p, i) => {
        const leftPct = (p.time / duration) * 100
        return (
          <button
            key={i}
            title={`${EMOTION_LABEL[p.emotion]} peak — ${fmt(p.time)}`}
            style={{ left: `${leftPct}%`, transform: "translateX(-50%)" }}
            className="absolute text-sm leading-none hover:scale-125 transition-transform"
            onClick={() => onSeek?.(p.time)}
          >
            {EMOTION_EMOJI[p.emotion]}
          </button>
        )
      })}
    </div>
  )
}

function TimestampRuler({ duration }: { duration: number }) {
  const ticks = Math.min(10, Math.floor(duration / 30))
  const step = duration / (ticks || 1)
  return (
    <div className="relative h-4 text-[10px] text-zinc-600">
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const t = i * step
        const pct = (t / duration) * 100
        return (
          <span key={i} className="absolute" style={{ left: `${pct}%`, transform: "translateX(-50%)" }}>
            {fmt(t)}
          </span>
        )
      })}
    </div>
  )
}

function EmotionLegend({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts)
    .filter(([k]) => k !== "silence")
    .sort(([, a], [, b]) => b - a)
  if (!entries.length) return null

  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {entries.map(([emotion, count]) => (
        <span key={emotion} className="flex items-center gap-1 text-xs text-zinc-400">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{ background: EMOTION_COLORS[emotion] ?? "#6b7280" }}
          />
          {EMOTION_EMOJI[emotion]} {EMOTION_LABEL[emotion]}
          <span className="text-zinc-600">({count})</span>
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- main component

interface Props {
  jobId: string | number
  /** Dipanggil saat user klik titik waktu — untuk seek video player */
  onSeek?: (seconds: number) => void
}

export function AudioEmotionTimeline({ jobId, onSeek }: Props) {
  const [open, setOpen]       = useState(false)
  const [status, setStatus]   = useState<"idle" | "running" | "done" | "error">("idle")
  const [result, setResult]   = useState<AudioResult | null>(null)
  const [errMsg, setErrMsg]   = useState("")
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = null
  }

  const startAnalysis = async () => {
    setStatus("running")
    setErrMsg("")
    try {
      await api.post("/api/analyzer/audio", { job_id: Number(jobId) })
    } catch {
      // 409 = sudah cached/running, lanjut polling saja
    }

    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/analyzer/audio/${jobId}/result`)
        if (data.status === "done") {
          setResult(data.result as AudioResult)
          setStatus("done")
          setOpen(true)
          stopPoll()
        } else if (data.status === "error") {
          setErrMsg((data.error as string) ?? "Gagal menganalisis audio.")
          setStatus("error")
          stopPoll()
        }
      } catch {
        // masih loading
      }
    }, 1500)
  }

  useEffect(() => () => stopPoll(), [])

  return (
    <div className="card space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Activity size={14} />
          Audio Emotion
          {result && (
            <span className="normal-case text-zinc-500 font-normal">
              — dominan: {EMOTION_EMOJI[result.summary.dominant_emotion]}{" "}
              {EMOTION_LABEL[result.summary.dominant_emotion]}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-2">
          {status === "idle" && (
            <button
              onClick={startAnalysis}
              className="text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
            >
              Analyze
            </button>
          )}
          {status === "running" && (
            <span className="flex items-center gap-1.5 text-xs text-zinc-500">
              <Loader2 size={12} className="animate-spin" />
              Analyzing…
            </span>
          )}
          {status === "error" && (
            <span className="text-xs text-red-400">{errMsg}</span>
          )}
          {result && (
            <button onClick={() => setOpen(o => !o)} className="text-zinc-500 hover:text-zinc-300 transition-colors">
              {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          )}
        </div>
      </div>

      {/* Timeline (collapsed by default) */}
      {open && result && (
        <div className="space-y-2 pt-1">
          {/* Emotion color strip */}
          <EmotionBar windows={result.windows} duration={result.duration_s} onSeek={onSeek} />

          {/* Energy waveform bars */}
          <EnergyBars windows={result.windows} duration={result.duration_s} onSeek={onSeek} />

          {/* Peak moment markers */}
          <PeakMarkers peaks={result.summary.peak_moments} duration={result.duration_s} onSeek={onSeek} />

          {/* Timestamp ruler */}
          <TimestampRuler duration={result.duration_s} />

          {/* Legend */}
          <EmotionLegend counts={result.summary.emotion_counts} />

          {/* Segment list */}
          {result.segments.length > 0 && (
            <div className="mt-2 border-t border-zinc-800 pt-2 space-y-1 max-h-40 overflow-y-auto">
              {result.segments.map((s, i) => (
                <button
                  key={i}
                  onClick={() => onSeek?.(s.start)}
                  className="w-full flex items-center gap-2 text-left text-xs px-2 py-1 rounded hover:bg-zinc-800 transition-colors group"
                >
                  <span
                    className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{ background: EMOTION_COLORS[s.emotion] ?? "#6b7280" }}
                  />
                  <span className="text-zinc-500 w-20 flex-shrink-0 font-mono">
                    {fmt(s.start)} – {fmt(s.end)}
                  </span>
                  <span className="text-zinc-300">
                    {EMOTION_EMOJI[s.emotion]} {s.description}
                  </span>
                  <span className="ml-auto text-zinc-600 group-hover:text-zinc-400">
                    energy {(s.avg_energy * 100).toFixed(0)}%
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
