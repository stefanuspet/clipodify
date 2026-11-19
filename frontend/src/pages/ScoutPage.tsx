import { useEffect, useState } from "react"
import { Radar, Plus, Trash2, Search, Loader2, Star } from "lucide-react"
import { api } from "../lib/api"

type Channel = { channel_id: string; handle: string; title: string; subscribers: number }
type Episode = {
  video_id: string; title: string; channel_title: string
  published_at: string; duration_str: string; view_count: number; source: string
}

export function ScoutPage() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [handle, setHandle] = useState("")
  const [scanning, setScanning] = useState(false)
  const [adding, setAdding] = useState(false)
  const [sortModes, setSortModes] = useState<string[]>([])
  const [sortMode, setSortMode] = useState("")
  const [recommendation, setRecommendation] = useState("")

  useEffect(() => {
    api.get("/api/scout/channels").then(r => setChannels(r.data))
    api.get("/api/scout/sort-modes").then(r => { setSortModes(r.data); setSortMode(r.data[0] ?? "") })
  }, [])

  async function addChannel() {
    if (!handle.trim()) return
    setAdding(true)
    try {
      const { data } = await api.post("/api/scout/channels", { handle: handle.trim() })
      setChannels(prev => [...prev, data]); setHandle("")
    } finally { setAdding(false) }
  }

  async function removeChannel(id: string) {
    await api.delete(`/api/scout/channels/${id}`)
    setChannels(prev => prev.filter(c => c.channel_id !== id))
  }

  async function scan() {
    setScanning(true); setEpisodes([])
    try {
      const { data } = await api.post("/api/scout/scan", {
        max_age_hours: 168, min_minutes: 10, sort_mode: sortMode, wide: false,
      })
      setEpisodes(data.episodes)
    } finally { setScanning(false) }
  }

  async function getRecommendation() {
    if (!episodes.length) return
    const { data } = await api.post("/api/scout/recommend", { episodes: episodes.slice(0, 20) })
    setRecommendation(data.recommendation)
  }

  return (
    <div className="max-w-4xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Radar size={24} className="text-brand-500" /> Podcast Scout
      </h1>

      {/* Tambah channel */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-sm text-zinc-400 uppercase tracking-wide">Channel Dipantau</h2>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="@handle atau channel ID"
            value={handle}
            onChange={e => setHandle(e.target.value)}
            onKeyDown={e => e.key === "Enter" && addChannel()}
          />
          <button className="btn-primary" onClick={addChannel} disabled={adding || !handle.trim()}>
            {adding ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Tambah
          </button>
        </div>
        <div className="space-y-1">
          {channels.map(c => (
            <div key={c.channel_id} className="flex items-center justify-between py-2 px-3 rounded-lg bg-zinc-800">
              <div>
                <span className="text-sm font-medium">{c.title}</span>
                <span className="text-zinc-500 text-xs ml-2">{c.handle}</span>
              </div>
              <button className="btn-ghost text-red-400 text-xs py-1 px-2" onClick={() => removeChannel(c.channel_id)}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {channels.length === 0 && <p className="text-zinc-500 text-sm">Belum ada channel.</p>}
        </div>
      </div>

      {/* Scan */}
      <div className="flex items-center gap-3">
        <select
          className="input w-auto"
          value={sortMode}
          onChange={e => setSortMode(e.target.value)}
        >
          {sortModes.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <button className="btn-primary" onClick={scan} disabled={scanning || channels.length === 0}>
          {scanning ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          Scan Episode Terbaru
        </button>
        {episodes.length > 0 && (
          <button className="btn-ghost" onClick={getRecommendation}>
            <Star size={14} /> Rekomendasi
          </button>
        )}
      </div>

      {recommendation && (
        <div className="card bg-brand-600/10 border-brand-600/30 text-sm whitespace-pre-wrap text-zinc-200">
          {recommendation}
        </div>
      )}

      {episodes.length > 0 && (
        <div className="space-y-2">
          {episodes.map(ep => (
            <div key={ep.video_id} className="card flex items-start gap-4 py-3">
              <img
                src={`https://img.youtube.com/vi/${ep.video_id}/mqdefault.jpg`}
                alt=""
                className="w-28 h-16 object-cover rounded-lg shrink-0"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium line-clamp-2">{ep.title}</p>
                <p className="text-zinc-500 text-xs mt-0.5">
                  {ep.channel_title} · {ep.duration_str} · {ep.view_count?.toLocaleString()} views
                </p>
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <span className={`badge text-xs ${ep.source === "dipantau" ? "bg-brand-600/20 text-brand-400" : "bg-zinc-800 text-zinc-400"}`}>
                  {ep.source}
                </span>
                <a
                  href={`https://youtube.com/watch?v=${ep.video_id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-zinc-500 hover:text-zinc-300"
                >
                  Buka ↗
                </a>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
