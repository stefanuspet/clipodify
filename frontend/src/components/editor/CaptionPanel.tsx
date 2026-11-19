import { useEffect, useState } from "react"
import { Copy, CheckCircle2, Loader2, ChevronDown, ChevronUp, Hash } from "lucide-react"
import { api } from "../../lib/api"

interface CaptionData {
  title:       string | null
  description: string | null
  hashtags:    string[]
  caption:     string | null
}

interface Props {
  candidateId: string | number
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  async function copy() {
    await navigator.clipboard.writeText(text)
    setDone(true)
    setTimeout(() => setDone(false), 2000)
  }
  return (
    <button
      onClick={copy}
      className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border border-zinc-700
        hover:border-zinc-500 text-zinc-400 hover:text-zinc-200 transition-colors"
    >
      {done
        ? <><CheckCircle2 size={12} className="text-green-400" /> Copied!</>
        : <><Copy size={12} /> {label}</>
      }
    </button>
  )
}

export function CaptionPanel({ candidateId }: Props) {
  const [data, setData]       = useState<CaptionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen]       = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [edited, setEdited]   = useState("")

  useEffect(() => {
    setLoading(true)
    api.get(`/api/candidates/${candidateId}/caption`)
      .then(r => { setData(r.data); setEdited(r.data.caption ?? "") })
      .finally(() => setLoading(false))
  }, [candidateId])

  const captionText = editMode ? edited : (data?.caption ?? "")

  return (
    <div className="card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Hash size={14} /> Caption
        </h2>
        <button
          onClick={() => setOpen(o => !o)}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-zinc-600 text-sm">
          <Loader2 size={13} className="animate-spin" /> Memuat caption…
        </div>
      )}

      {!loading && data && !open && (
        /* Collapsed: tampilkan satu baris + tombol copy cepat */
        <div className="flex items-center gap-2">
          <p className="text-xs text-zinc-500 truncate flex-1">
            {data.title ?? data.description ?? "—"}
          </p>
          <CopyButton text={captionText} label="Copy caption" />
        </div>
      )}

      {!loading && data && open && (
        <div className="space-y-4">
          {/* Title */}
          {data.title && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[10px] uppercase tracking-wide text-zinc-500">Judul</label>
                <CopyButton text={data.title} label="Copy" />
              </div>
              <p className="text-sm text-zinc-200 font-medium leading-snug">{data.title}</p>
            </div>
          )}

          {/* Description */}
          {data.description && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[10px] uppercase tracking-wide text-zinc-500">Deskripsi</label>
                <CopyButton text={data.description} label="Copy" />
              </div>
              <p className="text-xs text-zinc-400 leading-relaxed">{data.description}</p>
            </div>
          )}

          {/* Hashtags */}
          {data.hashtags?.length > 0 && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[10px] uppercase tracking-wide text-zinc-500">Hashtag</label>
                <CopyButton text={data.hashtags.map(h => `#${h}`).join(" ")} label="Copy" />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {data.hashtags.map(tag => (
                  <span key={tag} className="text-xs px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400">
                    #{tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Full caption — editable */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <label className="text-[10px] uppercase tracking-wide text-zinc-500">Full Caption</label>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => { setEditMode(e => !e); if (editMode) setEdited(data.caption ?? "") }}
                  className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors"
                >
                  {editMode ? "Cancel" : "Edit"}
                </button>
                <CopyButton text={captionText} label="Copy all" />
              </div>
            </div>

            {editMode ? (
              <textarea
                value={edited}
                onChange={e => setEdited(e.target.value)}
                rows={8}
                className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-3 py-2.5 text-xs
                  text-zinc-200 leading-relaxed resize-none focus:outline-none focus:border-brand-500 transition-colors"
              />
            ) : (
              <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl px-3 py-2.5">
                <p className="text-xs text-zinc-400 leading-relaxed whitespace-pre-wrap">{captionText}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
