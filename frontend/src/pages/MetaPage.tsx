import { useEffect, useState } from "react"
import { Camera, Unlink, Loader2, RefreshCw } from "lucide-react"
import { api } from "../lib/api"

type MetaStatus = {
  connected: boolean; kind?: string
  page_name?: string; ig_username?: string
}

export function MetaPage() {
  const [status, setStatus] = useState<MetaStatus | null>(null)
  const [token, setToken] = useState("")
  const [appId, setAppId] = useState("")
  const [appSecret, setAppSecret] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [pages, setPages] = useState<any[]>([])
  const [workingToken, setWorkingToken] = useState("")
  const [snapshot, setSnapshot] = useState<any>(null)

  useEffect(() => { loadStatus() }, [])

  async function loadStatus() {
    const { data } = await api.get("/api/meta/status")
    setStatus(data)
  }

  async function connect() {
    setLoading(true); setError(""); setPages([])
    try {
      const { data } = await api.post("/api/meta/connect", {
        token, app_id: appId, app_secret: appSecret,
      })
      if (data.connected) {
        await loadStatus()
      } else if (data.pages) {
        setPages(data.pages); setWorkingToken(data.working_token)
      }
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "Gagal connect.")
    } finally { setLoading(false) }
  }

  async function selectPage(page: any) {
    setLoading(true)
    try {
      await api.post("/api/meta/connect/page", {
        working_token: workingToken,
        page_id: page.page_id,
        page_name: page.page_name,
        ig_id: page.ig_id,
        ig_username: page.ig_username,
      })
      setPages([]); await loadStatus()
    } finally { setLoading(false) }
  }

  async function disconnect() {
    await api.delete("/api/meta/disconnect")
    setStatus({ connected: false }); setSnapshot(null)
  }

  async function fetchData() {
    setLoading(true)
    try {
      const { data } = await api.post("/api/meta/data", { limit: 25 })
      setSnapshot(data)
    } finally { setLoading(false) }
  }

  return (
    <div className="max-w-3xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Camera size={24} className="text-pink-500" /> Meta Analytics
      </h1>

      {!status?.connected && (
        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">Connect Instagram / Facebook</h2>
          <div>
            <label className="label">Access Token</label>
            <input className="input" value={token} onChange={e => setToken(e.target.value)} placeholder="EAA... atau IGAA..." />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="label">App ID (opsional)</label>
              <input className="input" value={appId} onChange={e => setAppId(e.target.value)} placeholder="123456789" />
            </div>
            <div className="flex-1">
              <label className="label">App Secret (opsional)</label>
              <input className="input" type="password" value={appSecret} onChange={e => setAppSecret(e.target.value)} />
            </div>
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button className="btn-primary" onClick={connect} disabled={loading || !token.trim()}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : null}
            Connect
          </button>

          {pages.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-zinc-800">
              <p className="text-sm text-zinc-400">Pilih Facebook Page:</p>
              {pages.map((p: any) => (
                <button
                  key={p.page_id}
                  onClick={() => selectPage(p)}
                  className="w-full text-left card py-3 hover:border-brand-600 transition-colors"
                >
                  <p className="font-medium">{p.page_name}</p>
                  {p.ig_username && <p className="text-zinc-400 text-xs">@{p.ig_username}</p>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {status?.connected && (
        <div className="card flex items-center justify-between">
          <div>
            <p className="font-semibold">{status.page_name ?? status.ig_username}</p>
            <p className="text-zinc-400 text-xs">{status.kind} · {status.ig_username && `@${status.ig_username}`}</p>
          </div>
          <div className="flex gap-2">
            <button className="btn-ghost" onClick={fetchData} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              Fetch Data
            </button>
            <button className="btn-ghost text-red-400" onClick={disconnect}>
              <Unlink size={14} /> Disconnect
            </button>
          </div>
        </div>
      )}

      {snapshot && (
        <div className="card space-y-3">
          <h3 className="font-semibold">Snapshot</h3>
          <pre className="text-xs text-zinc-400 overflow-auto max-h-96">
            {JSON.stringify(snapshot, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}
