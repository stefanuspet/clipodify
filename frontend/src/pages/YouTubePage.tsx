import { useEffect, useState } from "react"
import { Tv2, RefreshCw, Unlink, Loader2, TrendingUp } from "lucide-react"
import { api } from "../lib/api"

type YtStatus = { connected: boolean; channel_title?: string; channel_id?: string }
type Video = { video_id: string; title: string; published_at: string; view_count: number; like_count: number }

export function YouTubePage() {
  const [status, setStatus] = useState<YtStatus | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(false)
  const [advice, setAdvice] = useState("")

  useEffect(() => { loadStatus() }, [])

  async function loadStatus() {
    const { data } = await api.get("/api/youtube/status")
    setStatus(data)
    if (data.connected) loadVideos()
  }

  async function loadVideos() {
    const { data } = await api.get("/api/youtube/videos")
    setVideos(data)
  }

  async function refresh() {
    setLoading(true)
    try {
      await api.post("/api/youtube/videos/refresh", { limit: 50 })
      await loadVideos()
    } finally { setLoading(false) }
  }

  async function disconnect() {
    await api.delete("/api/youtube/disconnect")
    setStatus({ connected: false }); setVideos([])
  }

  async function getAdvice() {
    setLoading(true)
    try {
      const { data } = await api.post("/api/youtube/advice/clip")
      setAdvice(data.advice)
    } finally { setLoading(false) }
  }

  return (
    <div className="max-w-4xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Tv2 size={24} className="text-red-500" /> YouTube Analytics
      </h1>

      <div className="card flex items-center justify-between">
        {status?.connected ? (
          <>
            <div>
              <p className="font-semibold">{status.channel_title}</p>
              <p className="text-zinc-400 text-sm">{status.channel_id}</p>
            </div>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={refresh} disabled={loading}>
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Refresh
              </button>
              <button className="btn-ghost text-red-400" onClick={disconnect}>
                <Unlink size={14} /> Disconnect
              </button>
            </div>
          </>
        ) : (
          <div className="space-y-3 w-full">
            <p className="text-zinc-400 text-sm">
              Belum terhubung. Lihat Settings untuk setup Google OAuth.
            </p>
          </div>
        )}
      </div>

      {status?.connected && videos.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{videos.length} Videos</h2>
            <button className="btn-ghost text-xs" onClick={getAdvice} disabled={loading}>
              <TrendingUp size={14} /> Clip Advice
            </button>
          </div>

          {advice && (
            <div className="card bg-brand-600/10 border-brand-600/30 text-sm whitespace-pre-wrap text-zinc-200">
              {advice}
            </div>
          )}

          <div className="space-y-2">
            {videos.slice(0, 20).map(v => (
              <div key={v.video_id} className="card flex items-center gap-4 py-3">
                <img
                  src={`https://img.youtube.com/vi/${v.video_id}/mqdefault.jpg`}
                  alt=""
                  className="w-24 h-14 object-cover rounded-lg shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{v.title}</p>
                  <p className="text-zinc-500 text-xs mt-0.5">
                    {v.view_count?.toLocaleString()} views · {v.like_count?.toLocaleString()} likes
                  </p>
                </div>
                <span className="text-zinc-500 text-xs shrink-0">{v.published_at?.slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
