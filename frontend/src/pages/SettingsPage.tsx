import { useEffect, useState } from "react"
import { Settings, Plus, Trash2, CheckCircle2, Loader2 } from "lucide-react"
import { api } from "../lib/api"

type ApiKey = { id: number; name: string; key_masked: string; is_active: boolean }
type TmpUsage = { file_count: number; total_mb: number }

export function SettingsPage() {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [name, setName] = useState("")
  const [key, setKey] = useState("")
  const [adding, setAdding] = useState(false)
  const [tmp, setTmp] = useState<TmpUsage | null>(null)
  const [clearingTmp, setClearingTmp] = useState(false)
  const [keyMsg, setKeyMsg] = useState("")
  const [gfKey, setGfKey] = useState("")
  const [gfState, setGfState] = useState<{ set: boolean; masked: string } | null>(null)
  const [gfMsg, setGfMsg] = useState("")

  useEffect(() => {
    api.get("/api/settings/keys").then(r => setKeys(r.data))
    api.get("/api/settings/tmp").then(r => setTmp(r.data))
    api.get("/api/settings/integrations").then(r => setGfState(r.data.google_fonts_api_key))
  }, [])

  async function saveGfKey(value: string | null) {
    setGfMsg("")
    const { data } = await api.put("/api/settings/integrations", { google_fonts_api_key: value })
    setGfState(data.google_fonts_api_key); setGfKey("")
    if (value) {
      try {
        const { data: t } = await api.post("/api/settings/integrations/google-fonts/test")
        setGfMsg(`Terhubung — ${t.families} font tersedia.`)
      } catch (e: any) { setGfMsg(e?.response?.data?.detail ?? "Key ditolak.") }
    }
  }

  async function addKey() {
    if (!name.trim() || !key.trim()) return
    setAdding(true); setKeyMsg("")
    try {
      const { data: added } = await api.post("/api/settings/keys", { name: name.trim(), key_value: key.trim() })
      const { data } = await api.get("/api/settings/keys")
      setKeys(data); setName(""); setKey("")
      try {
        const { data: t } = await api.post(`/api/settings/keys/${added.id}/test`)
        setKeyMsg(t.ok ? `Key "${added.name}" tersimpan dan berfungsi.` : `Key tersimpan, tapi uji gagal: ${t.message}`)
      } catch { setKeyMsg(`Key "${added.name}" tersimpan (belum bisa diuji).`) }
    } catch (e: any) {
      const d = e?.response?.data?.detail
      setKeyMsg(typeof d === "string" ? d : "Gagal menyimpan key.")
    } finally { setAdding(false) }
  }

  async function activateKey(id: number) {
    await api.post(`/api/settings/keys/${id}/activate`)
    setKeys(prev => prev.map(k => ({ ...k, is_active: k.id === id })))
  }

  async function deleteKey(id: number) {
    await api.delete(`/api/settings/keys/${id}`)
    setKeys(prev => prev.filter(k => k.id !== id))
  }

  async function clearTmp() {
    setClearingTmp(true)
    try {
      await api.delete("/api/settings/tmp")
      const { data } = await api.get("/api/settings/tmp")
      setTmp(data)
    } finally { setClearingTmp(false) }
  }

  return (
    <div className="max-w-2xl mx-auto py-10 px-4 space-y-8">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Settings size={24} className="text-brand-500" /> Settings
      </h1>

      {/* API Keys */}
      <section className="card space-y-4">
        <h2 className="font-semibold">Gemini API Keys</h2>
        <div className="flex gap-2">
          <input className="input flex-1" placeholder="Nama (mis. key-1)" value={name} onChange={e => setName(e.target.value)} />
          <input className="input flex-1" placeholder="AIza..." value={key} onChange={e => setKey(e.target.value)} type="password" />
          <button className="btn-primary" onClick={addKey} disabled={adding || !name || !key}>
            {adding ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          </button>
        </div>
        {keyMsg && <p className="text-xs text-brand-400">{keyMsg}</p>}

        <div className="space-y-1">
          {keys.map(k => (
            <div key={k.id} className={`flex items-center gap-3 px-3 py-2 rounded-lg ${k.is_active ? "bg-brand-600/10 border border-brand-600/30" : "bg-zinc-800"}`}>
              <div className="flex-1 min-w-0">
                <span className="text-sm font-medium">{k.name}</span>
                <span className="text-zinc-500 text-xs ml-2">{k.key_masked}</span>
              </div>
              {k.is_active && <CheckCircle2 size={14} className="text-green-400 shrink-0" />}
              {!k.is_active && (
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => activateKey(k.id)}>Aktifkan</button>
              )}
              <button className="btn-ghost text-red-400 text-xs py-1 px-2" onClick={() => deleteKey(k.id)}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {keys.length === 0 && <p className="text-zinc-500 text-sm">Belum ada API key.</p>}
        </div>
      </section>

      {/* Integrasi */}
      <section className="card space-y-3">
        <h2 className="font-semibold">Google Fonts</h2>
        <p className="text-zinc-400 text-sm">
          API key dari Google Cloud Console (aktifkan "Web Fonts Developer API"). Dipakai Studio Subtitle & Gemini
          untuk memilih dari seluruh katalog Google Fonts (semua berlisensi bebas).
        </p>
        <p className="text-sm">
          Status: {gfState?.set ? <span className="text-green-400">terisi ({gfState.masked})</span>
                                : <span className="text-amber-400">belum diisi — memakai font dasar</span>}
        </p>
        <div className="flex gap-2">
          <input type="password" value={gfKey} onChange={e => setGfKey(e.target.value)} placeholder="AIza…"
            className="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm" />
          <button className="btn-primary" disabled={!gfKey.trim()} onClick={() => saveGfKey(gfKey.trim())}>Simpan & uji</button>
          {gfState?.set && <button className="btn-ghost text-red-400" onClick={() => saveGfKey(null)}>Hapus</button>}
        </div>
        {gfMsg && <p className="text-xs text-brand-400">{gfMsg}</p>}
      </section>

      {/* Tmp usage */}
      <section className="card space-y-3">
        <h2 className="font-semibold">Temporary Files</h2>
        {tmp && (
          <p className="text-zinc-400 text-sm">
            {tmp.file_count} file · {tmp.total_mb.toFixed(1)} MB
          </p>
        )}
        <button className="btn-ghost text-red-400" onClick={clearTmp} disabled={clearingTmp}>
          {clearingTmp ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          Hapus semua tmp
        </button>
      </section>
    </div>
  )
}
