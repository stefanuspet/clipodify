import { useEffect, useRef, useState } from "react"
import { Play, Pause, Volume2, VolumeX, Maximize2, RefreshCw } from "lucide-react"
import { api } from "../../lib/api"

// ---------------------------------------------------------------- types

interface YouTubeMeta {
  type: "youtube"
  url: string
  start_seconds: number
  duration_seconds: number
  video_id: string
}

interface Props {
  candidateId: string | number
  /** Klik timestamp dari AudioEmotionTimeline → seek di sini */
  seekTo?: number | null
  onSeekHandled?: () => void
}

// ---------------------------------------------------------------- YouTube embed helper

function YouTubeEmbed({ meta }: { meta: YouTubeMeta }) {
  const start = Math.floor(meta.start_seconds)
  const end   = Math.floor(meta.start_seconds + meta.duration_seconds)
  const src   = `https://www.youtube.com/embed/${meta.video_id}?start=${start}&end=${end}&autoplay=0&rel=0`

  return (
    <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
      <iframe
        src={src}
        className="absolute inset-0 w-full h-full rounded-lg"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
      />
    </div>
  )
}

// ---------------------------------------------------------------- main component

export function ClipPreview({ candidateId, seekTo, onSeekHandled }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [status, setStatus]   = useState<"loading" | "ready" | "yt" | "error">("loading")
  const [ytMeta, setYtMeta]   = useState<YouTubeMeta | null>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted]     = useState(false)
  const [progress, setProgress] = useState(0)   // 0-1
  const [duration, setDuration] = useState(0)
  const [errMsg, setErrMsg]   = useState("")

  const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"
  const previewUrl = `${API_BASE}/api/candidates/${candidateId}/preview`

  // Probe preview endpoint
  useEffect(() => {
    setStatus("loading")
    setErrMsg("")

    api.get(`/api/candidates/${candidateId}/preview`, { responseType: "blob" })
      .then(res => {
        const ct = String(res.headers["content-type"] ?? "")
        if (ct.includes("video")) {
          // Local video — blob URL agar tidak re-fetch
          const url = URL.createObjectURL(res.data as Blob)
          if (videoRef.current) videoRef.current.src = url
          setStatus("ready")
        } else {
          // JSON response (YouTube)
          const reader = new FileReader()
          reader.onload = () => {
            try {
              const meta = JSON.parse(reader.result as string) as YouTubeMeta
              setYtMeta(meta)
              setStatus("yt")
            } catch {
              setErrMsg("Format response tidak dikenali.")
              setStatus("error")
            }
          }
          reader.readAsText(res.data as Blob)
        }
      })
      .catch(err => {
        setErrMsg(err?.response?.data?.detail ?? "Preview tidak tersedia.")
        setStatus("error")
      })

    return () => {
      if (videoRef.current?.src?.startsWith("blob:")) {
        URL.revokeObjectURL(videoRef.current.src)
      }
    }
  }, [candidateId])

  // Seek when AudioEmotionTimeline klik timestamp
  useEffect(() => {
    if (seekTo != null && videoRef.current && status === "ready") {
      videoRef.current.currentTime = seekTo
      onSeekHandled?.()
    }
  }, [seekTo])

  // ---- video controls
  const togglePlay = () => {
    if (!videoRef.current) return
    if (playing) videoRef.current.pause()
    else videoRef.current.play()
  }

  const toggleMute = () => {
    if (!videoRef.current) return
    videoRef.current.muted = !muted
    setMuted(m => !m)
  }

  const handleTimeUpdate = () => {
    const v = videoRef.current
    if (!v || !v.duration) return
    setProgress(v.currentTime / v.duration)
  }

  const seekBar = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!videoRef.current || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const pct  = (e.clientX - rect.left) / rect.width
    videoRef.current.currentTime = pct * duration
  }

  function fmt(s: number) {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, "0")}`
  }

  return (
    <div className="card space-y-2">
      <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">
        Clip Preview
      </h2>

      {/* Loading */}
      {status === "loading" && (
        <div className="flex items-center justify-center h-40 rounded-lg bg-zinc-900 border border-zinc-800">
          <RefreshCw size={18} className="animate-spin text-zinc-600" />
        </div>
      )}

      {/* Error */}
      {status === "error" && (
        <div className="flex items-center justify-center h-24 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-500 text-sm">
          {errMsg}
        </div>
      )}

      {/* YouTube embed */}
      {status === "yt" && ytMeta && (
        <YouTubeEmbed meta={ytMeta} />
      )}

      {/* Local video player */}
      {(status === "loading" || status === "ready") && (
        <div className={status === "loading" ? "hidden" : ""}>
          {/* Video element — 9:16 aspect ratio */}
          <div
            className="relative rounded-lg overflow-hidden bg-black cursor-pointer mx-auto"
            style={{ aspectRatio: "9/16", maxHeight: "420px" }}
            onClick={togglePlay}
          >
            <video
              ref={videoRef}
              className="w-full h-full object-contain"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={() => setDuration(videoRef.current?.duration ?? 0)}
              onEnded={() => setPlaying(false)}
              playsInline
            />
            {/* Play overlay */}
            {!playing && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-14 h-14 rounded-full bg-black/60 flex items-center justify-center backdrop-blur-sm">
                  <Play size={24} className="text-white ml-1" />
                </div>
              </div>
            )}
          </div>

          {/* Controls bar */}
          <div className="space-y-1.5 pt-1">
            {/* Progress bar */}
            <div
              className="h-1.5 w-full bg-zinc-800 rounded-full cursor-pointer overflow-hidden"
              onClick={seekBar}
            >
              <div
                className="h-full bg-brand-500 rounded-full transition-none"
                style={{ width: `${progress * 100}%` }}
              />
            </div>

            {/* Buttons + time */}
            <div className="flex items-center gap-2">
              <button onClick={togglePlay} className="text-zinc-400 hover:text-white transition-colors">
                {playing ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <button onClick={toggleMute} className="text-zinc-400 hover:text-white transition-colors">
                {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
              </button>
              <span className="text-xs text-zinc-600 font-mono ml-1">
                {fmt(progress * duration)} / {fmt(duration)}
              </span>
              <a
                href={previewUrl}
                download
                className="ml-auto text-zinc-600 hover:text-zinc-400 transition-colors"
                title="Download preview"
              >
                <Maximize2 size={14} />
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
