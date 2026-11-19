import { useState, useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"
import { Scissors, Loader2, ChevronDown, ChevronUp, Megaphone } from "lucide-react"
import { api } from "../lib/api"
import { ProgressBar } from "../components/ProgressBar"
import { useJobWs } from "../lib/hooks"

const LANGUAGES = [
  { value: "id",   label: "Indonesia" },
  { value: "en",   label: "English" },
  { value: null,   label: "Auto-detect" },
]

const MODES = [
  { value: "regular",  label: "Regular",  desc: "Clip terbaik untuk viral — engagement, hook, storytelling" },
  { value: "softsell", label: "Soft Sell", desc: "Clip yang natural menampilkan produk tanpa terasa iklan" },
  { value: "campaign", label: "Campaign",  desc: "Fokus pada momen yang paling relevan dengan campaign produk" },
]

export function AnalyzePage() {
  const navigate = useNavigate()

  // Core
  const [url, setUrl]         = useState("")
  const [loading, setLoading] = useState(false)
  const [jobId, setJobId]     = useState<string | null>(null)
  const [error, setError]     = useState("")
  const { latest }            = useJobWs(jobId)
  const pollRef               = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fallback polling — kalau WebSocket tidak kirim done (emit gagal), poll DB setiap 5 detik
  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/jobs/${jobId}`)
        if (data.status === "done" || data.status === "failed") {
          clearInterval(pollRef.current!)
          navigate(`/history?job=${jobId}`)
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [jobId])

  // Options
  const [showOptions, setShowOptions] = useState(false)
  const [minSec, setMinSec]   = useState(30)
  const [maxSec, setMaxSec]   = useState(90)
  const [language, setLanguage] = useState<string | null>("id")
  const [mode, setMode]       = useState("regular")
  const [useHint, setUseHint] = useState(false)

  // Campaign fields
  const [product, setProduct] = useState("")
  const [benefit, setBenefit] = useState("")
  const [cta, setCta]         = useState("")
  const [hashtags, setHashtags] = useState("")

  const isCampaign = mode === "campaign" || mode === "softsell"

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!url.trim()) return
    setLoading(true); setError("")
    try {
      const { data } = await api.post("/api/jobs", {
        src: url.trim(),
        min_seconds: minSec,
        max_seconds: maxSec,
        language,
        mode,
        use_performance_hint: useHint,
        campaign_product:  product.trim(),
        campaign_benefit:  benefit.trim(),
        campaign_cta:      cta.trim(),
        campaign_hashtags: hashtags.split(/[\s,]+/).filter(Boolean),
      })
      setJobId(String(data.job_id))
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "Gagal memulai analisis.")
      setLoading(false)
    }
  }

  if (latest?.type === "done" && jobId) {
    navigate(`/history?job=${jobId}`)
  }

  return (
    <div className="max-w-2xl mx-auto py-10 px-4 space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Scissors size={24} className="text-brand-500" /> Analyze
        </h1>
        <p className="text-zinc-400 text-sm mt-1">
          URL YouTube atau path file lokal — Gemini akan memilih clip terbaik.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* URL input */}
        <div className="card space-y-4">
          <div>
            <label className="label">URL atau path file</label>
            <input
              className="input"
              placeholder="https://youtube.com/watch?v=... atau /path/to/file.mp4"
              value={url}
              onChange={e => setUrl(e.target.value)}
              disabled={loading}
            />
          </div>

          {error && (
            <p className="text-red-400 text-sm bg-red-900/20 px-3 py-2 rounded-lg">{error}</p>
          )}

          {latest?.type === "progress" && (
            <ProgressBar
              step={latest.step}
              total={latest.total}
              label={latest.label}
              detail={latest.detail}
            />
          )}

          <button
            type="submit"
            className="btn-primary w-full justify-center"
            disabled={loading || !url.trim()}
          >
            {loading
              ? <Loader2 size={16} className="animate-spin" />
              : <Scissors size={16} />}
            {loading ? "Analyzing…" : "Analyze"}
          </button>
        </div>

        {/* Options toggle */}
        <button
          type="button"
          onClick={() => setShowOptions(o => !o)}
          className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-300 transition-colors w-full"
        >
          {showOptions ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          {showOptions ? "Sembunyikan opsi" : "Tampilkan opsi"}
        </button>

        {showOptions && (
          <div className="card space-y-5">
            {/* Duration */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Durasi Clip</h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-zinc-500 text-xs flex justify-between">
                    Minimum <span className="text-zinc-300">{minSec}s</span>
                  </label>
                  <input
                    type="range" min="10" max="120" step="5"
                    value={minSec}
                    onChange={e => {
                      const v = Number(e.target.value)
                      setMinSec(v)
                      if (v >= maxSec) setMaxSec(Math.min(300, v + 15))
                    }}
                    className="w-full accent-brand-500"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-zinc-500 text-xs flex justify-between">
                    Maksimum <span className="text-zinc-300">{maxSec}s</span>
                  </label>
                  <input
                    type="range" min="20" max="300" step="5"
                    value={maxSec}
                    onChange={e => {
                      const v = Number(e.target.value)
                      setMaxSec(v)
                      if (v <= minSec) setMinSec(Math.max(10, v - 15))
                    }}
                    className="w-full accent-brand-500"
                  />
                </div>
              </div>
              <p className="text-xs text-zinc-600">
                Gemini akan memilih clip antara {minSec}–{maxSec} detik.
              </p>
            </div>

            {/* Language */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Bahasa Transcript</h3>
              <div className="flex gap-2 flex-wrap">
                {LANGUAGES.map(l => (
                  <button
                    key={String(l.value)}
                    type="button"
                    onClick={() => setLanguage(l.value)}
                    className={`px-3 py-1.5 rounded-lg text-sm border transition-colors
                      ${language === l.value
                        ? "border-brand-500 bg-brand-600/10 text-brand-400"
                        : "border-zinc-700 text-zinc-400 hover:border-zinc-500"}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Mode */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Mode Analisis</h3>
              <div className="space-y-2">
                {MODES.map(m => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => setMode(m.value)}
                    className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors
                      ${mode === m.value
                        ? "border-brand-500 bg-brand-600/10"
                        : "border-zinc-700 hover:border-zinc-600"}`}
                  >
                    <span className={`text-sm font-medium ${mode === m.value ? "text-brand-400" : "text-zinc-300"}`}>
                      {m.label}
                    </span>
                    <p className="text-xs text-zinc-500 mt-0.5">{m.desc}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* Campaign fields — hanya tampil kalau campaign/softsell */}
            {isCampaign && (
              <div className="space-y-3 border-t border-zinc-800 pt-4">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400 flex items-center gap-2">
                  <Megaphone size={12} /> Campaign Info
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="label text-xs">Nama Produk / Brand *</label>
                    <input
                      className="input text-sm"
                      placeholder="mis. Tokopedia, Nike, Kopi Kenangan…"
                      value={product}
                      onChange={e => setProduct(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="label text-xs">Benefit / Value Proposition</label>
                    <input
                      className="input text-sm"
                      placeholder="mis. hemat 50%, gratis ongkir, kualitas premium…"
                      value={benefit}
                      onChange={e => setBenefit(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="label text-xs">Call to Action</label>
                    <input
                      className="input text-sm"
                      placeholder="mis. Download sekarang, Coba gratis 7 hari…"
                      value={cta}
                      onChange={e => setCta(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="label text-xs">Hashtag <span className="text-zinc-600">(pisah spasi/koma)</span></label>
                    <input
                      className="input text-sm"
                      placeholder="#tokopedia #belanjamudah #promo"
                      value={hashtags}
                      onChange={e => setHashtags(e.target.value)}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Performance hint */}
            <div className="flex items-center justify-between border-t border-zinc-800 pt-3">
              <div>
                <p className="text-sm text-zinc-300">Gunakan data performa channel</p>
                <p className="text-xs text-zinc-500">Gemini mempertimbangkan video mana yang sudah viral di channelmu</p>
              </div>
              <button
                type="button"
                onClick={() => setUseHint(h => !h)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                  ${useHint ? "bg-brand-600" : "bg-zinc-700"}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform
                  ${useHint ? "translate-x-6" : "translate-x-1"}`}
                />
              </button>
            </div>
          </div>
        )}
      </form>
    </div>
  )
}
