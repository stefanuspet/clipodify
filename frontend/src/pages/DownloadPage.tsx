import { useState } from "react"
import { Download, Loader2 } from "lucide-react"
import { api } from "../lib/api"

export function DownloadPage() {
  const [url, setUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState("")

  async function fetchMeta() {
    if (!url.trim()) return
    setLoading(true); setError(""); setResult(null)
    try {
      const { data } = await api.post("/api/download/metadata", { url: url.trim() })
      setResult(data)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "Gagal fetch metadata.")
    } finally {
      setLoading(false)
    }
  }

  async function downloadFull() {
    if (!url.trim()) return
    setLoading(true); setError("")
    try {
      await api.post("/api/download/full", { url: url.trim() })
      setError(""); alert("Download dimulai di background.")
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "Gagal download.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Download size={24} className="text-brand-500" /> Download
      </h1>

      <div className="card space-y-4">
        <div>
          <label className="label">URL YouTube</label>
          <input
            className="input"
            placeholder="https://youtube.com/watch?v=..."
            value={url}
            onChange={e => setUrl(e.target.value)}
          />
        </div>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <div className="flex gap-3">
          <button className="btn-ghost" onClick={fetchMeta} disabled={loading || !url.trim()}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : null}
            Fetch Info
          </button>
          <button className="btn-primary" onClick={downloadFull} disabled={loading || !url.trim()}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            Full Download
          </button>
        </div>
      </div>

      {result && (
        <div className="card space-y-2">
          {result.thumbnail && (
            <img src={result.thumbnail} alt="thumbnail" className="rounded-lg w-full object-cover max-h-48" />
          )}
          <p className="font-semibold">{result.title}</p>
          <p className="text-zinc-400 text-sm">{result.channel} · {result.duration_str}</p>
          {result.description && (
            <p className="text-zinc-500 text-xs line-clamp-3">{result.description}</p>
          )}
        </div>
      )}
    </div>
  )
}
