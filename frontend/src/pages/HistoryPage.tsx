import { useEffect, useState } from "react"
import { useSearchParams, useNavigate } from "react-router-dom"
import {
  Clock, ChevronRight, Loader2, Download, Scissors, Copy,
  Star, MessageSquare, Zap, TrendingUp, CheckCircle2, AlertCircle, Trash2, RefreshCw,
} from "lucide-react"
import { api } from "../lib/api"

// ---------------------------------------------------------------- types

type Job = {
  id: number; url: string; title: string; status: string; created_at: string
  thumbnail: string | null; duration: number | null; source_type: string | null
  candidates_count: number; mode: string | null; campaign_product: string | null
}

type Candidate = {
  id: number
  title: string | null
  start: string; end: string
  start_seconds: number; duration_seconds: number
  final_score: number; shareability: number | null
  hook: string | null; reason: string | null; description: string | null
  clip_type: string | null; status: string | null; output_path: string | null
  hook_strength: number | null; payoff_strength: number | null
  campaign_fit: number | null; product_moment: string | null
  discussion_trigger: number | null
}

// ---------------------------------------------------------------- helpers

const STATUS_COLOR: Record<string, string> = {
  done:       "text-green-400 bg-green-900/20",
  running:    "text-yellow-400 bg-yellow-900/20",
  pending:    "text-zinc-400 bg-zinc-800",
  error:      "text-red-400 bg-red-900/20",
  downloaded: "text-blue-400 bg-blue-900/20",
  skipped:    "text-zinc-600 bg-zinc-900",
}

const CLIP_TYPE_ICON: Record<string, string> = {
  hook:        "🎯",
  story:       "📖",
  insight:     "💡",
  viral_moment:"🔥",
  softsell:    "🎪",
  campaign:    "📢",
  highlight:   "⭐",
}

function fmt(s: number) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, "0")}`
}

function ScoreBar({ value, label, color = "bg-brand-500" }: { value: number | null; label: string; color?: string }) {
  if (value == null) return null
  // Scores from Gemini are 0–10; normalize to percentage
  const pct = Math.round(value * 10)
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-zinc-500 w-20 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-zinc-400 w-6 text-right">{pct}</span>
    </div>
  )
}

// ---------------------------------------------------------------- CandidateCard

function CandidateCard({
  cand, jobId, rank, onDownloaded, onFileDeleted
}: {
  cand: Candidate; jobId: number; rank: number
  onDownloaded: (id: number, path: string) => void
  onFileDeleted: (id: number) => void
}) {
  const navigate = useNavigate()
  const [downloading, setDownloading] = useState(false)
  const [deleting, setDeleting]       = useState(false)
  const [copied, setCopied]           = useState(false)
  const [expanded, setExpanded]       = useState(false)

  const duration = Math.round(cand.duration_seconds)
  // Gemini scores are 0–10
  const scoreColor = cand.final_score >= 7
    ? "text-green-400" : cand.final_score >= 5
    ? "text-yellow-400" : "text-zinc-500"

  async function download() {
    setDownloading(true)
    try {
      await api.post(`/api/candidates/${cand.id}/download`, { job_id: jobId, candidate_id: cand.id })
      onDownloaded(cand.id, "")
    } catch { /* will show in status */ } finally {
      setDownloading(false)
    }
  }

  async function copyCaption() {
    try {
      const { data } = await api.get(`/api/candidates/${cand.id}/caption`)
      await navigator.clipboard.writeText(data.caption ?? data.description ?? "")
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }

  return (
    <div className={`rounded-xl border transition-colors
      ${cand.status === "skipped" ? "border-zinc-800 opacity-50" : "border-zinc-800 hover:border-zinc-700"}`}
    >
      {/* Top row */}
      <div className="flex items-start gap-3 p-4">
        {/* Rank + score badge */}
        <div className="flex flex-col items-center gap-1 shrink-0 pt-0.5">
          <span className="text-zinc-600 text-xs font-mono">#{rank}</span>
          <span className={`text-sm font-bold ${scoreColor}`}>
            {cand.final_score?.toFixed(1)}
          </span>
        </div>

        {/* Main info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {cand.clip_type && (
              <span className="text-xs">{CLIP_TYPE_ICON[cand.clip_type] ?? "📎"}</span>
            )}
            <span className="font-medium text-sm truncate">
              {cand.title || `Clip ${rank} · ${fmt(cand.start_seconds)} – ${fmt(cand.start_seconds + (cand.duration_seconds ?? 0))}`}
            </span>
            <span className={`badge ml-auto shrink-0 ${STATUS_COLOR[cand.status ?? "pending"] ?? STATUS_COLOR.pending}`}>
              {cand.status}
            </span>
          </div>

          <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500">
            <span className="font-mono">{fmt(cand.start_seconds)} → {fmt(cand.start_seconds + cand.duration_seconds)}</span>
            <span>{duration}s</span>
            {cand.clip_type && (
              <span className="text-zinc-600 capitalize">{cand.clip_type.replace("_", " ")}</span>
            )}
          </div>

          {/* Hook — most important Gemini output */}
          {cand.hook && (
            <p className="text-sm text-zinc-300 mt-2 line-clamp-2 leading-snug">
              "{cand.hook}"
            </p>
          )}
        </div>
      </div>

      {/* Score bars + details (expandable) */}
      {expanded && (
        <div className="px-4 pb-3 space-y-3 border-t border-zinc-800 pt-3">
          {/* Scores */}
          <div className="space-y-1.5">
            <ScoreBar value={cand.shareability}       label="Shareability"  color="bg-brand-500" />
            <ScoreBar value={cand.hook_strength}      label="Hook"         color="bg-orange-500" />
            <ScoreBar value={cand.payoff_strength}    label="Payoff"       color="bg-green-600" />
            <ScoreBar value={cand.discussion_trigger} label="Discussion"   color="bg-purple-500" />
            {cand.campaign_fit != null && (
              <ScoreBar value={cand.campaign_fit}     label="Campaign fit" color="bg-yellow-500" />
            )}
          </div>

          {/* Reason / description */}
          {cand.reason && (
            <p className="text-xs text-zinc-400 leading-relaxed">{cand.reason}</p>
          )}

          {/* Product moment */}
          {cand.product_moment && (
            <div className="text-xs text-zinc-500 bg-zinc-900 rounded-lg px-3 py-2">
              <span className="text-brand-400 font-medium">Product moment: </span>
              {cand.product_moment}
            </div>
          )}
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-1 px-3 pb-3 pt-1">
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-xs text-zinc-600 hover:text-zinc-400 px-2 py-1 rounded transition-colors"
        >
          {expanded ? "Sembunyikan" : "Detail →"}
        </button>

        <div className="flex-1" />

        {/* Copy caption */}
        <button
          onClick={copyCaption}
          title="Copy caption"
          className="p-1.5 rounded-lg text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
        >
          {copied ? <CheckCircle2 size={14} className="text-green-400" /> : <Copy size={14} />}
        </button>

        {/* Download / Re-download */}
        {cand.status !== "downloaded" ? (
          <button
            onClick={download}
            disabled={downloading}
            title="Download clip"
            className="p-1.5 rounded-lg text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-40"
          >
            {downloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
          </button>
        ) : (
          <div className="flex items-center gap-0.5">
            <CheckCircle2 size={14} className="text-blue-400" />
            <button
              onClick={async () => {
                setDeleting(true)
                try {
                  await api.delete(`/api/candidates/${cand.id}/file`)
                  onFileDeleted(cand.id)
                } finally { setDeleting(false) }
              }}
              disabled={deleting}
              title="Hapus file & download ulang dengan kualitas terbaik"
              className="p-1.5 rounded-lg text-zinc-600 hover:text-red-400 hover:bg-zinc-800 transition-colors disabled:opacity-40"
            >
              {deleting ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            </button>
          </div>
        )}

        {/* Edit */}
        <button
          onClick={() => navigate(`/editor?job=${jobId}&candidate=${cand.id}`)}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white transition-colors ml-1"
        >
          Edit <ChevronRight size={12} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- JobItem

function JobItem({
  job, selected, onClick, onDelete,
}: {
  job: Job; selected: boolean; onClick: () => void; onDelete: (id: number) => void
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const isYT = job.source_type === "youtube" || job.url?.includes("youtube") || job.url?.includes("youtu.be")

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirmDelete) { setConfirmDelete(true); return }
    setDeleting(true)
    try {
      await api.delete(`/api/jobs/${job.id}`)
      onDelete(job.id)
    } catch { setDeleting(false); setConfirmDelete(false) }
  }

  return (
    <div
      className={`relative border-b border-zinc-800 group ${selected ? "bg-zinc-800" : ""}`}
    >
      <button
        onClick={onClick}
        className="w-full text-left hover:bg-zinc-800/60 transition-colors"
      >
        {/* Thumbnail strip */}
        {isYT && job.thumbnail && (
          <div className="relative w-full h-24 overflow-hidden">
            <img src={job.thumbnail} alt="" className="w-full h-full object-cover" />
            <div className="absolute inset-0 bg-gradient-to-t from-zinc-900 to-transparent" />
            {job.candidates_count > 0 && (
              <span className="absolute bottom-1.5 right-2 text-[10px] font-mono bg-black/70 text-zinc-300 px-1.5 py-0.5 rounded">
                {job.candidates_count} clips
              </span>
            )}
            {job.duration && (
              <span className="absolute bottom-1.5 left-2 text-[10px] font-mono bg-black/70 text-zinc-300 px-1.5 py-0.5 rounded">
                {fmt(job.duration)}
              </span>
            )}
          </div>
        )}
        <div className="px-4 py-3 pr-10">
          <p className="text-sm font-medium line-clamp-2 leading-snug">{job.title || job.url}</p>
          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
            <span className={`badge text-[10px] ${STATUS_COLOR[job.status] ?? STATUS_COLOR.pending}`}>
              {job.status}
            </span>
            {job.mode && job.mode !== "regular" && (
              <span className="badge text-[10px] text-purple-400 bg-purple-900/20">{job.mode}</span>
            )}
            {job.campaign_product && (
              <span className="text-[10px] text-zinc-600 truncate max-w-[80px]">{job.campaign_product}</span>
            )}
            <span className="text-zinc-600 text-[10px] ml-auto">{job.created_at?.slice(0, 10)}</span>
          </div>
        </div>
      </button>

      {/* Tombol hapus — muncul saat hover atau confirm */}
      <button
        onClick={handleDelete}
        onBlur={() => setConfirmDelete(false)}
        disabled={deleting}
        title={confirmDelete ? "Klik lagi untuk konfirmasi hapus" : "Hapus job ini"}
        className={`absolute top-2 right-2 p-1.5 rounded-lg transition-all
          ${confirmDelete
            ? "bg-red-700 text-white opacity-100"
            : "opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 hover:bg-zinc-700"
          } disabled:opacity-40`}
      >
        {deleting
          ? <Loader2 size={13} className="animate-spin" />
          : <Trash2 size={13} />
        }
      </button>
    </div>
  )
}

// ---------------------------------------------------------------- main page

export function HistoryPage() {
  const [params]   = useSearchParams()
  const [jobs, setJobs]           = useState<Job[]>([])
  const [selectedJob, setSelectedJob] = useState<number | null>(
    params.get("job") ? Number(params.get("job")) : null
  )
  const [candidates, setCandidates]   = useState<Candidate[]>([])
  const [loadingCandidates, setLoadingCandidates] = useState(false)
  const [sortBy, setSortBy]           = useState<"score" | "time">("score")

  useEffect(() => {
    api.get("/api/jobs").then(r => setJobs(r.data))
  }, [])

  useEffect(() => {
    if (!selectedJob) return
    setLoadingCandidates(true)
    api.get(`/api/candidates/job/${selectedJob}`)
      .then(r => setCandidates(r.data))
      .finally(() => setLoadingCandidates(false))
  }, [selectedJob])

  const sorted = [...candidates].sort((a, b) =>
    sortBy === "score"
      ? (b.final_score ?? 0) - (a.final_score ?? 0)
      : a.start_seconds - b.start_seconds
  )

  const selectedJobData = jobs.find(j => j.id === selectedJob)

  function markDownloaded(id: number) {
    setCandidates(cs => cs.map(c => c.id === id ? { ...c, status: "downloaded" } : c))
  }

  function markFileDeleted(id: number) {
    setCandidates(cs => cs.map(c => c.id === id ? { ...c, status: "pending", output_path: null } : c))
  }

  function handleJobDeleted(id: number) {
    setJobs(js => js.filter(j => j.id !== id))
    if (selectedJob === id) { setSelectedJob(null); setCandidates([]) }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ---- Job list (left sidebar) */}
      <div className="w-72 shrink-0 border-r border-zinc-800 overflow-y-auto flex flex-col">
        <div className="px-4 py-4 border-b border-zinc-800 shrink-0">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <Clock size={15} className="text-brand-500" /> Jobs
          </h2>
        </div>
        {jobs.length === 0 && (
          <p className="text-zinc-500 text-sm px-4 py-6">Belum ada job.</p>
        )}
        {jobs.map(job => (
          <JobItem
            key={job.id}
            job={job}
            selected={selectedJob === job.id}
            onClick={() => setSelectedJob(job.id)}
            onDelete={handleJobDeleted}
          />
        ))}
      </div>

      {/* ---- Candidate panel (right) */}
      <div className="flex-1 overflow-y-auto">
        {!selectedJob && (
          <div className="flex flex-col items-center justify-center h-full text-zinc-500 gap-2">
            <Scissors size={32} className="text-zinc-700" />
            <span className="text-sm">Pilih job di kiri untuk melihat clips.</span>
          </div>
        )}

        {selectedJob && loadingCandidates && (
          <div className="flex items-center justify-center h-full gap-2 text-zinc-500">
            <Loader2 size={18} className="animate-spin" /> Loading…
          </div>
        )}

        {selectedJob && !loadingCandidates && (
          <>
            {/* Header */}
            <div className="px-5 py-4 border-b border-zinc-800 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-sm truncate">
                  {selectedJobData?.title ?? `Job #${selectedJob}`}
                </p>
                <p className="text-xs text-zinc-500 mt-0.5">
                  {candidates.length} clips ditemukan
                  {selectedJobData?.mode && selectedJobData.mode !== "regular" && (
                    <> · <span className="text-purple-400">{selectedJobData.mode}</span></>
                  )}
                  {selectedJobData?.campaign_product && (
                    <> · <span className="text-zinc-400">{selectedJobData.campaign_product}</span></>
                  )}
                </p>
              </div>

              {/* Sort toggle */}
              <div className="flex gap-1 bg-zinc-900 rounded-lg p-0.5 border border-zinc-800">
                {[
                  { key: "score" as const, icon: <Star size={12} />, label: "Score" },
                  { key: "time"  as const, icon: <Clock size={12} />, label: "Time" },
                ].map(opt => (
                  <button
                    key={opt.key}
                    onClick={() => setSortBy(opt.key)}
                    className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition-colors
                      ${sortBy === opt.key
                        ? "bg-zinc-700 text-zinc-200"
                        : "text-zinc-500 hover:text-zinc-300"}`}
                  >
                    {opt.icon} {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Stats row */}
            {candidates.length > 0 && (
              <div className="px-5 py-3 border-b border-zinc-800 grid grid-cols-4 gap-3">
                {[
                  {
                    icon: <TrendingUp size={13} className="text-brand-400" />,
                    label: "Avg Score",
                    value: `${(candidates.reduce((s, c) => s + (c.final_score ?? 0), 0) / candidates.length).toFixed(1)}`,
                  },
                  {
                    icon: <Zap size={13} className="text-orange-400" />,
                    label: "Avg Hook",
                    value: `${(candidates.reduce((s, c) => s + (c.hook_strength ?? 0), 0) / candidates.length).toFixed(1)}`,
                  },
                  {
                    icon: <MessageSquare size={13} className="text-purple-400" />,
                    label: "Avg Share",
                    value: `${(candidates.reduce((s, c) => s + (c.shareability ?? 0), 0) / candidates.length).toFixed(1)}`,
                  },
                  {
                    icon: <Download size={13} className="text-blue-400" />,
                    label: "Downloaded",
                    value: `${candidates.filter(c => c.status === "downloaded").length}/${candidates.length}`,
                  },
                ].map(stat => (
                  <div key={stat.label} className="bg-zinc-900/60 rounded-lg px-3 py-2">
                    <div className="flex items-center gap-1.5 text-zinc-500 mb-0.5">
                      {stat.icon}
                      <span className="text-[10px] uppercase tracking-wide">{stat.label}</span>
                    </div>
                    <p className="text-sm font-semibold text-zinc-200">{stat.value}</p>
                  </div>
                ))}
              </div>
            )}

            {/* Candidate cards */}
            {candidates.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-zinc-500 gap-2">
                <AlertCircle size={24} className="text-zinc-700" />
                <p className="text-sm">Belum ada candidate untuk job ini.</p>
              </div>
            ) : (
              <div className="p-4 space-y-3">
                {sorted.map((c, i) => (
                  <CandidateCard
                    key={c.id}
                    cand={c}
                    jobId={selectedJob}
                    rank={i + 1}
                    onDownloaded={markDownloaded}
                    onFileDeleted={markFileDeleted}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
