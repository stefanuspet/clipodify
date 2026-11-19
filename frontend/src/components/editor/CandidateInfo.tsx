import { useEffect, useState } from "react"
import { Loader2, TrendingUp, Zap, MessageSquare, Share2, ChevronDown, ChevronUp } from "lucide-react"
import { api } from "../../lib/api"

// ---------------------------------------------------------------- types

interface SignalNotes {
  comment?: string; audio?: string; narrative?: string; text?: string
}

interface Candidate {
  id: number
  title: string | null
  hook: string | null
  reason: string | null
  description: string | null
  clip_type: string | null
  start_seconds: number; end_seconds: number
  final_score: number
  shareability: number | null
  hook_strength: number | null
  payoff_strength: number | null
  discussion_trigger: number | null
  campaign_fit: number | null
  product_moment: string | null
  signal_notes: SignalNotes | null
  video_title: string | null
  mode: string | null
  campaign_product: string | null
}

// ---------------------------------------------------------------- helpers

const CLIP_TYPE_LABEL: Record<string, string> = {
  hook:         "🎯 Hook",
  story:        "📖 Story",
  insight:      "💡 Insight",
  viral_moment: "🔥 Viral",
  softsell:     "🎪 Soft Sell",
  campaign:     "📢 Campaign",
  highlight:    "⭐ Highlight",
  funny:        "😂 Funny",
}

const SIGNAL_ICONS: Record<string, string> = {
  comment: "💬", audio: "🎙️", narrative: "📖", text: "✍️",
}

function fmt(s: number) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, "0")}`
}

function MiniScore({ label, value, color }: { label: string; value: number | null; color: string }) {
  if (value == null) return null
  return (
    <div className="flex flex-col items-center gap-1">
      <span className={`text-sm font-bold ${color}`}>{value.toFixed(1)}</span>
      <span className="text-[10px] text-zinc-500 leading-none text-center">{label}</span>
    </div>
  )
}

// ---------------------------------------------------------------- component

interface Props {
  candidateId: string | number
}

export function CandidateInfo({ candidateId }: Props) {
  const [data, setData]       = useState<Candidate | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.get(`/api/candidates/${candidateId}`)
      .then(r => setData(r.data))
      .finally(() => setLoading(false))
  }, [candidateId])

  if (loading) return (
    <div className="card flex items-center gap-2 text-zinc-600 text-sm">
      <Loader2 size={14} className="animate-spin" /> Memuat info clip…
    </div>
  )

  if (!data) return null

  const duration = Math.round(data.end_seconds - data.start_seconds)
  const scoreColor = data.final_score >= 8 ? "text-green-400"
    : data.final_score >= 6 ? "text-yellow-400" : "text-zinc-400"

  return (
    <div className="card space-y-3">
      {/* Top row: title + score */}
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          {/* Clip type + duration */}
          <div className="flex items-center gap-2 mb-1">
            {data.clip_type && (
              <span className="text-xs text-zinc-500">
                {CLIP_TYPE_LABEL[data.clip_type] ?? data.clip_type}
              </span>
            )}
            <span className="text-xs text-zinc-600">
              {fmt(data.start_seconds)} → {fmt(data.end_seconds)} · {duration}s
            </span>
            {data.mode && data.mode !== "regular" && (
              <span className="badge text-[10px] text-purple-400 bg-purple-900/20">{data.mode}</span>
            )}
          </div>

          {/* Title */}
          <h2 className="font-semibold text-base leading-snug">
            {data.title || `Clip · ${fmt(data.start_seconds)}`}
          </h2>

          {/* Source video */}
          {data.video_title && (
            <p className="text-xs text-zinc-500 mt-0.5 truncate">dari: {data.video_title}</p>
          )}
        </div>

        {/* Final score */}
        <div className="shrink-0 flex flex-col items-center bg-zinc-900 rounded-xl px-3 py-2 border border-zinc-800">
          <span className={`text-xl font-bold ${scoreColor}`}>{data.final_score.toFixed(1)}</span>
          <span className="text-[10px] text-zinc-500">score</span>
        </div>
      </div>

      {/* Hook — the most important Gemini output */}
      {data.hook && (
        <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-4 py-3">
          <p className="text-xs text-zinc-500 mb-1 flex items-center gap-1.5">
            <Zap size={11} className="text-orange-400" /> HOOK
          </p>
          <p className="text-sm text-zinc-100 leading-relaxed italic">"{data.hook}"</p>
        </div>
      )}

      {/* Sub-scores */}
      <div className="flex justify-around py-1">
        <MiniScore label="Share" value={data.shareability}       color="text-brand-400" />
        <MiniScore label="Hook"  value={data.hook_strength}      color="text-orange-400" />
        <MiniScore label="Payoff" value={data.payoff_strength}   color="text-green-400" />
        <MiniScore label="Diskusi" value={data.discussion_trigger} color="text-purple-400" />
        {data.campaign_fit != null && (
          <MiniScore label="Campaign" value={data.campaign_fit}  color="text-yellow-400" />
        )}
      </div>

      {/* Expandable detail */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1.5 text-xs text-zinc-600 hover:text-zinc-400 transition-colors w-full pt-1 border-t border-zinc-800"
      >
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {expanded ? "Sembunyikan detail" : "Lihat alasan Gemini + sinyal"}
      </button>

      {expanded && (
        <div className="space-y-3 pt-1">
          {/* Reason */}
          {data.reason && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wide text-zinc-500 flex items-center gap-1.5">
                <TrendingUp size={10} /> Alasan dipilih
              </p>
              <p className="text-xs text-zinc-400 leading-relaxed">{data.reason}</p>
            </div>
          )}

          {/* Description */}
          {data.description && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wide text-zinc-500 flex items-center gap-1.5">
                <MessageSquare size={10} /> Deskripsi
              </p>
              <p className="text-xs text-zinc-400 leading-relaxed">{data.description}</p>
            </div>
          )}

          {/* Signal notes */}
          {data.signal_notes && Object.keys(data.signal_notes).length > 0 && (
            <div className="space-y-2">
              <p className="text-[10px] uppercase tracking-wide text-zinc-500 flex items-center gap-1.5">
                <Share2 size={10} /> Sinyal
              </p>
              <div className="grid grid-cols-1 gap-1.5">
                {Object.entries(data.signal_notes).map(([key, note]) => note ? (
                  <div key={key} className="flex gap-2 text-xs">
                    <span className="shrink-0 text-zinc-600">{SIGNAL_ICONS[key] ?? "·"}</span>
                    <span className="text-zinc-500">{note}</span>
                  </div>
                ) : null)}
              </div>
            </div>
          )}

          {/* Product moment */}
          {data.product_moment && (
            <div className="text-xs bg-zinc-900 rounded-lg px-3 py-2 border border-zinc-800">
              <span className="text-brand-400 font-medium">Product moment: </span>
              <span className="text-zinc-400">{data.product_moment}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
