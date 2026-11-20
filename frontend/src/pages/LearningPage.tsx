import { useEffect, useState } from "react"
import { GraduationCap, RefreshCw, Link2, Unlink, Loader2, Lightbulb, Star, Trash2 } from "lucide-react"
import { api } from "../lib/api"

type Suggestion = { video_id: string; title: string; published_at: string; similarity: number }
type LinkedVideo = {
  video_id: string; title: string; views: number | null; likes: number | null; comments: number | null
  shares: number | null; avg_view_percentage: number | null; published_at: string
}
type Render = {
  render_id: string; candidate_id: number; candidate_title: string | null; created_at: string
  model: string | null; intent: string | null; yt_video_id: string | null
  features: Record<string, any>; video: LinkedVideo | null; suggestions: Suggestion[]
}
type Group = { value: string; n: number; share_rate: number | null; comment_rate: number | null; retention: number | null }
type Comparison = { feature: string; label: string; groups: Group[]; enough_data: boolean }
type Best = { render_id: string; video_title: string; share_rate: number; comment_rate: number; views: number; intent: string }
type Overview = {
  renders: Render[]
  report: {
    status: { renders: number; linked: number; usable: number; missing_engagement: number }
    comparisons: Comparison[]; insights: string[]; best_plans: Best[]
    thresholds: { min_views: number; min_group: number; min_ratio: number }
  }
  persona: { id: string; name: string; examples: string[] }
  yt_last_fetch: string | null
}
type YtVideo = { video_id: string; title: string; published_at: string }

const pct = (v: number | null | undefined) => (v == null ? "–" : `${v.toFixed(2)}%`)

export function LearningPage() {
  const [data, setData] = useState<Overview | null>(null)
  const [videos, setVideos] = useState<YtVideo[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [msg, setMsg] = useState("")
  const [picked, setPicked] = useState<string[]>([])
  const [confirmHide, setConfirmHide] = useState<string | null>(null)

  async function load() {
    const [{ data: o }, { data: v }] = await Promise.all([
      api.get("/api/editor/learning"), api.get("/api/youtube/videos"),
    ])
    setData(o); setVideos(v)
  }
  useEffect(() => { load() }, [])

  async function refreshYouTube() {
    setRefreshing(true); setMsg("")
    try {
      await api.post("/api/youtube/videos/refresh", { limit: 50 })
      await load()
      setMsg("Data YouTube diperbarui.")
    } catch (e: any) {
      setMsg(e?.response?.data?.detail ?? "Gagal refresh YouTube.")
    } finally { setRefreshing(false) }
  }

  async function link(renderId: string, videoId: string | null) {
    await api.post(`/api/editor/learning/${renderId}/link`, { video_id: videoId })
    await load()
  }

  async function hideRender(renderId: string) {
    await api.delete(`/api/editor/learning/${renderId}`)
    setConfirmHide(null)
    await load()
  }

  async function applyExamples() {
    const { data: r } = await api.post("/api/editor/learning/apply-examples", { render_ids: picked })
    setMsg(`Persona diperbarui: ${r.examples.length} contoh plan dipakai Gemini mulai render berikutnya.`)
    setPicked([]); await load()
  }

  if (!data) return <div className="p-10 text-zinc-400 flex gap-2"><Loader2 className="animate-spin" size={16} /> Memuat…</div>
  const { report } = data
  const st = report.status

  return (
    <div className="max-w-4xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <GraduationCap size={24} className="text-brand-500" /> Learning
      </h1>
      <p className="text-sm text-zinc-400">
        Hubungkan render Auto Edit ke video YouTube-nya, lalu lihat pilihan edit mana yang menaikkan share dan komentar.
      </p>

      {/* Status */}
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-zinc-300 space-x-3">
          <span>{st.renders} render</span>
          <span>· {st.linked} terhubung</span>
          <span>· {st.usable} bisa dianalisis (≥{report.thresholds.min_views} views)</span>
          {st.missing_engagement > 0 && <span className="text-amber-400">· {st.missing_engagement} belum ada data share/komentar</span>}
        </div>
        <button className="btn-primary flex items-center gap-2" onClick={refreshYouTube} disabled={refreshing}>
          {refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          Refresh data YouTube
        </button>
        <p className="w-full text-xs text-zinc-500">
          Data YouTube terakhir diambil: {data.yt_last_fetch ?? "belum pernah"}
        </p>
        {msg && <p className="w-full text-xs text-brand-400">{msg}</p>}
      </div>

      {/* Insights */}
      <div className="card space-y-2">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Lightbulb size={14} /> Temuan
        </h2>
        {report.insights.length ? (
          <ul className="list-disc pl-5 text-sm text-zinc-200 space-y-1">
            {report.insights.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500">
            Belum ada temuan yang bisa dipercaya. Butuh minimal {report.thresholds.min_group} video terhubung
            (≥{report.thresholds.min_views} views) di setiap kelompok yang dibandingkan, dan selisih ≥{report.thresholds.min_ratio}×.
          </p>
        )}
        {report.comparisons.length > 0 && (
          <details className="text-xs text-zinc-400">
            <summary className="cursor-pointer">Tabel perbandingan lengkap</summary>
            <div className="space-y-3 mt-2">
              {report.comparisons.map(c => (
                <div key={c.feature}>
                  <p className="text-zinc-300">{c.label} {!c.enough_data && <span className="text-zinc-500">(belum cukup data)</span>}</p>
                  <table className="w-full mt-1">
                    <thead><tr className="text-zinc-500"><th className="text-left">Nilai</th><th>n</th><th>Share</th><th>Komentar</th><th>Retensi</th></tr></thead>
                    <tbody>
                      {c.groups.map(g => (
                        <tr key={g.value} className="text-center">
                          <td className="text-left">{g.value}</td><td>{g.n}</td><td>{pct(g.share_rate)}</td>
                          <td>{pct(g.comment_rate)}</td><td>{g.retention == null ? "–" : `${g.retention.toFixed(0)}%`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {/* Best plans → persona examples */}
      <div className="card space-y-2">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Star size={14} /> Plan terbaik → contoh untuk persona
        </h2>
        <p className="text-xs text-zinc-500">
          Persona aktif: {data.persona.name} · contoh saat ini: {data.persona.examples.length || "belum ada"}
        </p>
        {report.best_plans.length ? (
          <>
            {report.best_plans.map(b => (
              <label key={b.render_id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={picked.includes(b.render_id)}
                  onChange={e => setPicked(p => e.target.checked ? [...p, b.render_id] : p.filter(x => x !== b.render_id))} />
                <span className="text-zinc-200">{b.video_title}</span>
                <span className="text-zinc-500">· {b.intent} · share {pct(b.share_rate)} · komentar {pct(b.comment_rate)} · {b.views} views</span>
              </label>
            ))}
            <button className="btn-primary" disabled={!picked.length} onClick={applyExamples}>
              Jadikan contoh persona
            </button>
          </>
        ) : <p className="text-sm text-zinc-500">Belum ada video terhubung dengan data share/komentar.</p>}
      </div>

      {/* Renders */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">Render Auto Edit</h2>
        {data.renders.length === 0 && <p className="text-sm text-zinc-500">Belum ada render Auto Edit.</p>}
        {data.renders.map(r => (
          <div key={r.render_id} className="border border-zinc-800 rounded-lg p-3 space-y-1.5 text-sm">
            <div className="flex justify-between gap-2">
              <span className="text-zinc-200">{r.candidate_title ?? `Kandidat #${r.candidate_id}`}</span>
              <span className="flex items-center gap-2 text-xs text-zinc-500 whitespace-nowrap">
                {r.created_at.replace("T", " ")}
                {confirmHide === r.render_id ? (
                  <>
                    <button className="text-red-400 hover:underline" onClick={() => hideRender(r.render_id)}>hapus</button>
                    <button className="hover:underline" onClick={() => setConfirmHide(null)}>batal</button>
                  </>
                ) : (
                  <button title="Hapus dari daftar (file plan & video tidak dihapus)"
                    className="hover:text-red-400" onClick={() => setConfirmHide(r.render_id)}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
            <p className="text-xs text-zinc-500">
              {r.intent} · {r.model} · SFX {r.features.sfx_count ?? 0} · zoom {r.features.zoom_count ?? 0}
              {r.features.has_end_card && " · end card"}{r.features.has_teaser && " · teaser"}
              {r.features.generic_question && <span className="text-amber-400"> · pertanyaan generik</span>}
            </p>
            {r.video ? (
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="text-brand-400 flex items-center gap-1"><Link2 size={12} /> {r.video.title}</span>
                <span className="text-zinc-400">
                  {r.video.views ?? "–"} views · {r.video.shares ?? "–"} share · {r.video.comments ?? "–"} komentar
                </span>
                <button className="text-zinc-500 hover:text-red-400 flex items-center gap-1" onClick={() => link(r.render_id, null)}>
                  <Unlink size={12} /> lepas
                </button>
              </div>
            ) : (
              <select className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs"
                defaultValue="" onChange={e => e.target.value && link(r.render_id, e.target.value)}>
                <option value="">Hubungkan ke video YouTube…</option>
                {r.suggestions.length > 0 && (
                  <optgroup label="Saran (judul mirip, tayang setelah render)">
                    {r.suggestions.map(s => (
                      <option key={s.video_id} value={s.video_id}>{s.title} — mirip {(s.similarity * 100).toFixed(0)}%</option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="Semua video">
                  {videos.map(v => <option key={v.video_id} value={v.video_id}>{v.title} ({v.published_at?.slice(0, 10)})</option>)}
                </optgroup>
              </select>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
