import { useState, useRef, useEffect } from "react"
import { Grid2X2, Loader2, ChevronDown, ChevronUp, Download, Users } from "lucide-react"
import { api } from "../../lib/api"

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

const LAYOUT_OPTS = [
  { value: "auto",           label: "Auto — ikuti yang aktif per segmen" },
  { value: "speaker_focus",  label: "Speaker Focus — speaker dapat cell besar" },
]

interface Props {
  jobId:        string | number
  candidateId:  string | number
}

export function GridClipPanel({ jobId, candidateId }: Props) {
  const [open, setOpen]       = useState(false)
  const [status, setStatus]   = useState<"idle" | "running" | "done" | "error">("idle")
  const [layoutStyle, setLayoutStyle] = useState("auto")
  const [errMsg, setErrMsg]   = useState("")
  const [cacheKey, setCacheKey] = useState("")
  const [persons, setPersons] = useState<any[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stopPoll = () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null }
  useEffect(() => () => stopPoll(), [])

  const startGrid = async () => {
    setStatus("running"); setErrMsg(""); setPersons([])
    try {
      const { data } = await api.post("/api/analyzer/grid-clip", {
        job_id:       Number(jobId),
        candidate_id: Number(candidateId),
        layout_style: layoutStyle,
      })
      const key = (data.poll as string).split("/")[4] ?? `grid_${candidateId}`
      setCacheKey(key)

      if (data.status === "cached") {
        setPersons(data.result?.persons ?? [])
        setStatus("done"); setOpen(true); return
      }
    } catch (err: any) {
      setErrMsg(err?.response?.data?.detail ?? "Gagal memulai grid clip.")
      setStatus("error"); return
    }

    const key = `grid_${candidateId}`
    setCacheKey(key)
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/analyzer/grid-clip/${key}/status`)
        if (data.status === "done") {
          setPersons(data.persons ?? [])
          setStatus("done"); setOpen(true); stopPoll()
        } else if (data.status === "error") {
          setErrMsg(data.error ?? "Gagal render grid clip.")
          setStatus("error"); stopPoll()
        }
      } catch { /* retry */ }
    }, 2000)
  }

  const progressText: Record<string, string> = {
    "tracking persons...": "Tracking orang...",
    "detecting speakers": "Deteksi speaker...",
    "scoring activity...": "Scoring aktivitas...",
    "planning grid layout...": "Menyusun grid...",
    "rendering grid video...": "Render video...",
  }

  return (
    <div className="card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Grid2X2 size={14} />
          Grid Clip
          <span className="normal-case font-normal text-zinc-500">— portrait multi-person</span>
        </h2>
        <div className="flex items-center gap-2">
          {status === "idle" && (
            <button
              onClick={startGrid}
              className="text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
            >
              Generate
            </button>
          )}
          {status === "running" && (
            <span className="flex items-center gap-1.5 text-xs text-zinc-500">
              <Loader2 size={12} className="animate-spin" /> Processing…
            </span>
          )}
          {status === "error" && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-red-400">{errMsg}</span>
              <button onClick={() => setStatus("idle")} className="text-xs text-zinc-500 hover:text-zinc-300">Coba lagi</button>
            </div>
          )}
          {status === "done" && (
            <div className="flex items-center gap-2">
              <a
                href={`${API_BASE}/api/analyzer/grid-clip/${cacheKey}/file`}
                download
                className="flex items-center gap-1.5 text-xs px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors"
              >
                <Download size={11} /> Download Grid
              </a>
              <button onClick={() => setOpen(o => !o)} className="text-zinc-500 hover:text-zinc-300">
                {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Config — tampil saat idle atau running */}
      {status === "idle" && (
        <div className="space-y-3">
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="label">Layout style</label>
              <select
                className="input"
                value={layoutStyle}
                onChange={e => setLayoutStyle(e.target.value)}
              >
                {LAYOUT_OPTS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-zinc-600">
            Jumlah grid menyesuaikan otomatis — 2 orang di frame → 2 sel, 5 orang → 5 sel, dst. Siapa yang aktif/bicara tampil lebih besar.
          </p>
        </div>
      )}

      {/* Progress detail saat running */}
      {status === "running" && (
        <div className="text-xs text-zinc-500 space-y-1">
          <div className="flex gap-2 items-center"><Loader2 size={10} className="animate-spin" /> Tracking persons dengan MediaPipe...</div>
          <div className="flex gap-2 items-center text-zinc-600"><span className="w-3" /> Lip movement × audio RMS → siapa speaker</div>
          <div className="flex gap-2 items-center text-zinc-600"><span className="w-3" /> Optical flow → activity score</div>
          <div className="flex gap-2 items-center text-zinc-600"><span className="w-3" /> FFmpeg filter_complex → portrait grid</div>
          <p className="text-zinc-700 mt-1">Proses ini bisa memakan 2–5 menit tergantung panjang clip.</p>
        </div>
      )}

      {/* Result — persons detected */}
      {open && status === "done" && persons.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide flex items-center gap-1">
            <Users size={11} /> {persons.length} orang terdeteksi
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {persons.map((p: any) => (
              <div key={p.person_id} className="bg-zinc-800/50 rounded-lg p-2 text-xs space-y-0.5">
                <div className="font-semibold text-zinc-200">Orang {p.person_id}</div>
                <div className="text-zinc-500">{p.frame_count} frame</div>
                <div className="text-zinc-600">
                  {Math.floor(p.first_seen)}s – {Math.floor(p.last_seen)}s
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
