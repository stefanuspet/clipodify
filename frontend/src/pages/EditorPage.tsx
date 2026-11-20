import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Clapperboard, Loader2, Download, Play, Plus, Trash2, Wand2 } from "lucide-react"
import { api } from "../lib/api"
import { ProgressBar } from "../components/ProgressBar"
import { useRenderWs } from "../lib/hooks"
import { AudioEmotionTimeline } from "../components/editor/AudioEmotionTimeline"
import { SmartCropPanel } from "../components/editor/SmartCropPanel"
import { GridClipPanel } from "../components/editor/GridClipPanel"
import { ThumbnailPicker } from "../components/editor/ThumbnailPicker"
import { ClipPreview } from "../components/editor/ClipPreview"
import { CandidateInfo } from "../components/editor/CandidateInfo"
import { CaptionPanel } from "../components/editor/CaptionPanel"
import { TextOverlays, type TextOverlay } from "../components/editor/TextOverlays"
import { UploadPanel } from "../components/editor/UploadPanel"

type StudioStyle     = { id: string; name: string; favorite: boolean; created_by: string }
type Grade           = { contrast: number; saturation: number; brightness: number; gamma: number; warmth: number; tint: number }
const NEUTRAL_GRADE: Grade = { contrast: 1, saturation: 1, brightness: 0, gamma: 1, warmth: 0, tint: 0 }
type SfxItem         = { id: string; name: string; filename: string; category: string; duration_s: number; size_kb: number }
type SfxConfig       = { sfx_id: string; volume: number; timestamp: number }
type ExpressionType  = { id: string; label: string; emoji: string }
type ExpressionClip  = { id: string; type: string; start: number; end: number }

export function EditorPage() {
  const [params] = useSearchParams()
  const jobId       = params.get("job")
  const candidateId = params.get("candidate")

  const [styles, setStyles]              = useState<StudioStyle[]>([])
  const [sfxList, setSfxList]            = useState<SfxItem[]>([])
  const [exprTypes, setExprTypes]        = useState<ExpressionType[]>([])

  const [styleId, setStyleId]          = useState<string>("")
  const [grade, setGrade]              = useState<Grade>(NEUTRAL_GRADE)
  const [sfxConfig, setSfxConfig]      = useState<SfxConfig[]>([])
  const [expressions, setExpressions]  = useState<ExpressionClip[]>([])

  const [textOverlays, setTextOverlays] = useState<TextOverlay[]>([])
  const [clipDuration, setClipDuration] = useState(60)
  const [thumbUrl, setThumbUrl]         = useState<string | null>(null)

  const [seekTo, setSeekTo]       = useState<number | null>(null)
  const [renderId, setRenderId]   = useState<string | null>(null)
  const [renderStatus, setRenderStatus] = useState("")
  const [rendering, setRendering] = useState(false)
  const [autoPlan, setAutoPlan]   = useState<{ intent?: string; reasoning?: string; fixes?: string[]; model?: string; error?: string;
    persona?: string; beats?: { type: string; s: number; e: number; note: string }[];
    duration?: number; source_duration?: number; cut_seconds?: number; teaser_s?: number;
    music?: { title?: string; credit?: string | null } | null;
    rhythm?: { count: number; per_10s: number; avg_interval: number; max_gap: number; long_gaps: number;
      gaps: { s: number; e: number; len: number; long: boolean }[] };
    transitions?: { t: number; from: string; to: string; placed: boolean; reason?: string }[];
    build_ups?: { t: number; placed: boolean; riser?: string; hit?: string | null; reason?: string }[];
    gap_fills?: { gap: number[]; len: number; action: string; word?: string; t?: number }[];
    visual_transitions?: { t: number; kind: string }[];
    emojis?: { emoji: string; t: number }[];
    name_labels?: { name: string; role: string; t: number }[];
    music_moments?: { t: number; end: number; kind: string }[];
    people?: { id: string; name: string; role: string; confidence: number; speak_s?: number; evidence: string }[] } | null>(null)

  const { latest } = useRenderWs(renderId)

  useEffect(() => {
    api.get("/api/studio/styles").then(r => {
      setStyles(r.data)
      const fav = (r.data as StudioStyle[]).find(s => s.favorite) ?? r.data[0]
      if (fav) setStyleId(fav.id)
    })
    api.get("/api/editor/sfx").then(r => setSfxList(r.data))
    api.get("/api/editor/expressions").then(r => setExprTypes(r.data))
  }, [])

  useEffect(() => {
    if (!candidateId) return
    api.get(`/api/candidates/${candidateId}`)
      .then(r => {
        const dur = (r.data.end_seconds ?? 0) - (r.data.start_seconds ?? 0)
        if (dur > 0) setClipDuration(Math.ceil(dur))
        if (r.data.job_video_id)
          setThumbUrl(`https://img.youtube.com/vi/${r.data.job_video_id}/mqdefault.jpg`)
      })
      .catch(() => {})
  }, [candidateId])

  useEffect(() => {
    if (latest?.type === "done") setRenderStatus("done")
    if (latest?.type === "error") setRenderStatus("error")
  }, [latest])

  const renderPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => {
    if (!renderId || renderStatus === "done" || renderStatus === "error") {
      if (renderPollRef.current) { clearInterval(renderPollRef.current); renderPollRef.current = null }
      return
    }
    renderPollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/editor/render/${renderId}/status`)
        if (data.kind === "auto_plan" && (data.status === "done" || data.status === "error"))
          setAutoPlan({ intent: data.intent, reasoning: data.reasoning, fixes: data.fixes, model: data.model,
            error: data.error, persona: data.persona, beats: data.beats, duration: data.duration,
            source_duration: data.source_duration, cut_seconds: data.cut_seconds, teaser_s: data.teaser_s,
            music: data.music, people: data.people, rhythm: data.rhythm, transitions: data.transitions,
            build_ups: data.build_ups, gap_fills: data.gap_fills, visual_transitions: data.visual_transitions,
            emojis: data.emojis, name_labels: data.name_labels, music_moments: data.music_moments })
        if (data.status === "done") { setRenderStatus("done"); setRendering(false) }
        else if (data.status === "error") { setRenderStatus("error"); setRendering(false) }
      } catch { /* ignore */ }
    }, 3000)
    return () => { if (renderPollRef.current) { clearInterval(renderPollRef.current); renderPollRef.current = null } }
  }, [renderId, renderStatus])

  async function startRender() {
    if (!jobId || !candidateId) return
    setRendering(true); setRenderId(null); setRenderStatus("running")
    try {
      const { data } = await api.post("/api/editor/render", {
        job_id: Number(jobId),
        candidate_id: Number(candidateId),
        config: {
          subtitle_style_id: styleId || null,
          grade,
          sfx: sfxConfig,
          expressions: expressions.map(({ type, start, end }) => ({ type, start, end })),
          text_overlays: textOverlays.map(({ id: _id, ...rest }) => rest),
        },
      })
      setRenderId(data.render_id)
    } catch (err: any) {
      setRenderStatus("error"); setRendering(false)
    }
  }

  async function startAutoEdit() {
    if (!candidateId) return
    setRendering(true); setRenderId(null); setRenderStatus("running"); setAutoPlan(null)
    try {
      const { data } = await api.post("/api/editor/auto-plan", { candidate_id: Number(candidateId) })
      setRenderId(data.render_id)
    } catch {
      setRenderStatus("error"); setRendering(false)
    }
  }

  const isDone = renderStatus === "done"

  return (
    <div className="max-w-3xl mx-auto py-10 px-4 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Clapperboard size={24} className="text-brand-500" /> Video Editor
        </h1>
      </div>

      {(!jobId || !candidateId) && (
        <div className="card text-zinc-400 text-sm">
          Buka halaman ini dari <strong>History</strong> → klik tombol "Edit" pada candidate.
        </div>
      )}

      {jobId && candidateId && (
        <>
          {/* Candidate info */}
          <CandidateInfo candidateId={candidateId} />

          {/* Clip Preview player */}
          <ClipPreview
            candidateId={candidateId}
            seekTo={seekTo}
            onSeekHandled={() => setSeekTo(null)}
          />

          {/* Caption */}
          <CaptionPanel candidateId={candidateId} />

          {/* Subtitle (gaya dari Studio) */}
          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">Gaya Subtitle</h2>
              <a href="/studio" className="text-xs text-brand-400 hover:underline">Kelola di Studio →</a>
            </div>
            <select value={styleId} onChange={e => setStyleId(e.target.value)}
              className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
              <option value="">Tanpa subtitle</option>
              {styles.map(s => (
                <option key={s.id} value={s.id}>
                  {s.favorite ? "★ " : ""}{s.name}{s.created_by === "gemini" ? " (Gemini)" : ""}
                </option>
              ))}
            </select>
            <p className="text-xs text-zinc-500">Waktu kata mengikuti transkrip asli; posisi otomatis menghindari wajah.</p>
          </div>

          {/* Grade warna (resep) */}
          <div className="card space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">Grade Warna</h2>
              <button className="text-xs text-zinc-500 hover:text-zinc-300" onClick={() => setGrade(NEUTRAL_GRADE)}>reset</button>
            </div>
            {([
              ["contrast", "Kontras", 0.85, 1.4, 0.01],
              ["saturation", "Saturasi", 0.6, 1.5, 0.01],
              ["brightness", "Kecerahan", -0.1, 0.1, 0.005],
              ["gamma", "Gamma", 0.8, 1.2, 0.01],
              ["warmth", "Dingin ↔ Hangat", -1, 1, 0.05],
              ["tint", "Magenta ↔ Hijau", -1, 1, 0.05],
            ] as const).map(([k, label, min, max, step]) => (
              <label key={k} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-zinc-400 w-32">{label}</span>
                <input type="range" min={min} max={max} step={step} value={grade[k]}
                  onChange={e => setGrade(g => ({ ...g, [k]: Number(e.target.value) }))} className="flex-1 accent-brand-500" />
                <span className="w-12 text-right font-mono text-xs text-zinc-300">{grade[k].toFixed(2)}</span>
              </label>
            ))}
          </div>

          {/* SFX */}
          {sfxList.length > 0 && (
            <div className="card space-y-3">
              <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">Sound Effects</h2>
              <div className="space-y-2">
                {sfxList.map(s => {
                  const cfg = sfxConfig.find(c => c.sfx_id === s.id)
                  const isOn = !!cfg
                  function toggle() {
                    setSfxConfig(prev =>
                      isOn
                        ? prev.filter(c => c.sfx_id !== s.id)
                        : [...prev, { sfx_id: s.id, volume: 0.7, timestamp: 0 }]
                    )
                  }
                  function update(field: "volume" | "timestamp", val: number) {
                    setSfxConfig(prev =>
                      prev.map(c => c.sfx_id === s.id ? { ...c, [field]: val } : c)
                    )
                  }
                  return (
                    <div
                      key={s.id}
                      className={`rounded-xl border px-4 py-3 transition-colors ${
                        isOn ? "border-brand-600/40 bg-brand-600/5" : "border-zinc-800 bg-zinc-900/40"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          className="accent-brand-500 w-4 h-4 cursor-pointer"
                          checked={isOn}
                          onChange={toggle}
                        />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-medium text-zinc-100">{s.name ?? s.filename}</span>
                          <span className="ml-2 text-zinc-500 text-xs">{s.duration_s}s</span>
                        </div>
                      </div>
                      {isOn && (
                        <div className="mt-2.5 grid grid-cols-2 gap-3">
                          <div className="space-y-1">
                            <label className="text-zinc-500 text-xs flex justify-between">
                              Volume <span className="text-zinc-300">{cfg.volume.toFixed(1)}</span>
                            </label>
                            <input
                              type="range" min="0" max="2" step="0.1"
                              value={cfg.volume}
                              onChange={e => update("volume", parseFloat(e.target.value))}
                              className="w-full accent-brand-500"
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-zinc-500 text-xs flex justify-between">
                              Timestamp <span className="text-zinc-300">{cfg.timestamp.toFixed(1)}s</span>
                            </label>
                            <input
                              type="range" min="0" max="30" step="0.5"
                              value={cfg.timestamp}
                              onChange={e => update("timestamp", parseFloat(e.target.value))}
                              className="w-full accent-brand-500"
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Expressions */}
          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400">
                Expression Effects
              </h2>
              <button
                onClick={() => setExpressions(prev => [
                  ...prev,
                  { id: crypto.randomUUID(), type: exprTypes[0]?.id ?? "angry", start: 0, end: 1 }
                ])}
                className="btn-ghost text-xs gap-1"
                disabled={exprTypes.length === 0}
              >
                <Plus size={13} /> Add
              </button>
            </div>

            {expressions.length === 0 ? (
              <p className="text-zinc-600 text-xs text-center py-3">
                Tambah efek untuk momen lucu/dramatis — efek hanya aktif di rentang waktu yang dipilih
              </p>
            ) : (
              <div className="space-y-2">
                {expressions.map(expr => (
                  <div key={expr.id} className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2.5 space-y-2.5">
                    {/* Type selector */}
                    <div className="flex items-center gap-2 flex-wrap">
                      {exprTypes.map(t => (
                        <button
                          key={t.id}
                          onClick={() => setExpressions(prev => prev.map(e => e.id === expr.id ? { ...e, type: t.id } : e))}
                          className={`px-2.5 py-1 rounded-lg text-xs border transition-colors ${
                            expr.type === t.id
                              ? "border-brand-500 bg-brand-600/15 text-brand-300"
                              : "border-zinc-700 text-zinc-400 hover:border-zinc-600"
                          }`}
                        >
                          {t.emoji} {t.label}
                        </button>
                      ))}
                      <button
                        onClick={() => setExpressions(prev => prev.filter(e => e.id !== expr.id))}
                        className="ml-auto text-zinc-600 hover:text-red-400 transition-colors"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>

                    {/* Time range */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-zinc-500 text-xs flex justify-between">
                          Start <span className="text-zinc-300">{expr.start.toFixed(1)}s</span>
                        </label>
                        <input
                          type="range" min="0" max={clipDuration} step="0.5"
                          value={expr.start}
                          onChange={e => {
                            const v = parseFloat(e.target.value)
                            setExpressions(prev => prev.map(x => x.id === expr.id
                              ? { ...x, start: v, end: Math.max(x.end, v + 0.5) }
                              : x))
                          }}
                          className="w-full accent-brand-500"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-zinc-500 text-xs flex justify-between">
                          End <span className="text-zinc-300">{expr.end.toFixed(1)}s</span>
                        </label>
                        <input
                          type="range" min="0.5" max={clipDuration} step="0.5"
                          value={expr.end}
                          onChange={e => {
                            const v = parseFloat(e.target.value)
                            setExpressions(prev => prev.map(x => x.id === expr.id
                              ? { ...x, end: v, start: Math.min(x.start, v - 0.5) }
                              : x))
                          }}
                          className="w-full accent-brand-500"
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Text Overlays */}
          <TextOverlays
            overlays={textOverlays}
            clipDuration={clipDuration}
            onChange={setTextOverlays}
          />

          {/* Audio Emotion Timeline */}
          <AudioEmotionTimeline jobId={jobId} onSeek={setSeekTo} />

          {/* Smart Crop */}
          <SmartCropPanel jobId={jobId} candidateId={candidateId ?? undefined} />
          {candidateId && (
            <GridClipPanel jobId={jobId!} candidateId={candidateId} />
          )}

          {/* Thumbnail Picker */}
          <ThumbnailPicker jobId={jobId} candidateId={candidateId ?? undefined} onSeek={setSeekTo} />

          {/* Render */}
          <div className="card space-y-4">
            {latest?.type === "progress" && (
              <ProgressBar
                step={latest.step}
                total={latest.total}
                label={latest.label}
              />
            )}

            {renderStatus === "error" && latest?.type === "error" && (
              <p className="text-red-400 text-sm">{latest.message}</p>
            )}

            {autoPlan && (
              <div className="rounded-lg border border-zinc-700 p-3 text-xs space-y-1.5">
                {autoPlan.error
                  ? <p className="text-red-400">{autoPlan.error}</p>
                  : <>
                      <p className="text-zinc-300">
                        <span className="font-semibold text-purple-300">{autoPlan.intent}</span>
                        {autoPlan.model && <span className="text-zinc-500"> · {autoPlan.model}</span>}
                        {autoPlan.persona && <span className="text-zinc-500"> · {autoPlan.persona}</span>}
                      </p>
                      <p className="text-zinc-400 leading-snug">{autoPlan.reasoning}</p>
                      {autoPlan.duration != null && (
                        <p className="text-zinc-500">
                          Durasi {autoPlan.source_duration?.toFixed(1)}s → {autoPlan.duration.toFixed(1)}s
                          {!!autoPlan.cut_seconds && <> · jeda/potongan −{autoPlan.cut_seconds.toFixed(1)}s</>}
                          {!!autoPlan.teaser_s && <> · teaser {autoPlan.teaser_s.toFixed(1)}s</>}
                          {autoPlan.music?.title && <> · musik: {autoPlan.music.title}</>}
                        </p>
                      )}
                      {autoPlan.rhythm && (
                        <div className="text-zinc-500">
                          Ritme: {autoPlan.rhythm.count} perubahan · rata-rata tiap {autoPlan.rhythm.avg_interval.toFixed(1)}s
                          · celah terpanjang <span className={autoPlan.rhythm.max_gap >= 6 ? "text-amber-400" : ""}>
                            {autoPlan.rhythm.max_gap.toFixed(1)}s</span>
                          {autoPlan.rhythm.gaps.length > 0 && (
                            <span> · celah: {autoPlan.rhythm.gaps.map((g, i) => (
                              <span key={i} title="waktu di video hasil"
                                className={`mr-1 ${g.long ? "text-amber-400" : "text-zinc-400"}`}>
                                {g.s.toFixed(0)}–{g.e.toFixed(0)}s
                              </span>
                            ))}</span>
                          )}
                        </div>
                      )}
                      {!!autoPlan.transitions?.length && (
                        <p className="text-zinc-500" title={autoPlan.transitions.map(t =>
                          `${t.t.toFixed(1)}s ${t.from}→${t.to}: ${t.placed ? "whoosh" : "dilewati, " + t.reason}`).join("\n")}>
                          Whoosh transisi: {autoPlan.transitions.filter(t => t.placed).length} dipasang
                          {autoPlan.transitions.some(t => !t.placed) &&
                            <> · {autoPlan.transitions.filter(t => !t.placed).length} dilewati</>}
                        </p>
                      )}
                      {!!autoPlan.build_ups?.length && (
                        <p className="text-zinc-500">
                          Build-up: {autoPlan.build_ups.filter(b => b.placed).map(b => `${b.t.toFixed(1)}s`).join(", ") || "tidak ada"}
                        </p>
                      )}
                      {!!autoPlan.gap_fills?.length && (
                        <p className="text-zinc-500" title={autoPlan.gap_fills.map(g =>
                          `celah ${g.gap[0].toFixed(1)}–${g.gap[1].toFixed(1)}s: ${g.action}${g.word ? ` di "${g.word}"` : ""}`).join("\n")}>
                          Isi celah otomatis: {autoPlan.gap_fills.filter(g => g.t != null).length}×
                          ({autoPlan.gap_fills.filter(g => g.t != null).map(g => g.action).join(", ")})
                        </p>
                      )}
                      {(!!autoPlan.visual_transitions?.length || !!autoPlan.emojis?.length || !!autoPlan.name_labels?.length) && (
                        <p className="text-zinc-500">
                          {!!autoPlan.visual_transitions?.length && <>Transisi visual: {autoPlan.visual_transitions.length}× </>}
                          {!!autoPlan.emojis?.length && <>· Emoji: {autoPlan.emojis.map(e => `${e.emoji} ${e.t.toFixed(0)}s`).join(" ")} </>}
                          {!!autoPlan.name_labels?.length && <>· Label: {autoPlan.name_labels.map(l => l.name).join(", ")}</>}
                        </p>
                      )}
                      {!!autoPlan.music_moments?.length && (
                        <p className="text-zinc-500">
                          Musik mengikuti momen: {autoPlan.music_moments.map(m => `${m.kind} ${m.t.toFixed(0)}s`).join(" · ")}
                        </p>
                      )}
                      {autoPlan.music?.credit && (
                        <p className="text-amber-400">Wajib cantumkan di caption: {autoPlan.music.credit}</p>
                      )}
                      {!!autoPlan.people?.length && (
                        <p className="text-zinc-500">
                          Orang: {autoPlan.people.map(p => (
                            <span key={p.id} title={p.evidence} className={p.confidence >= 0.5 ? "text-zinc-300" : ""}>
                              {p.name} ({p.role}{p.speak_s ? `, bicara ${Math.round(p.speak_s)}s` : ""})
                            </span>
                          )).reduce<React.ReactNode[]>((acc, el, i) => (i ? [...acc, " · ", el] : [el]), [])}
                        </p>
                      )}
                      {!!autoPlan.beats?.length && (
                        <details className="text-zinc-500">
                          <summary className="cursor-pointer">{autoPlan.beats.length} momen (beat)</summary>
                          <ul className="mt-1 space-y-0.5">
                            {autoPlan.beats.map((b, i) => (
                              <li key={i}>
                                <button className="text-brand-400 hover:underline mr-1" onClick={() => setSeekTo(b.s)}>
                                  {b.s.toFixed(1)}s
                                </button>
                                <span className="text-zinc-300">{b.type}</span> — {b.note}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                      {!!autoPlan.fixes?.length && (
                        <details className="text-zinc-500">
                          <summary className="cursor-pointer">{autoPlan.fixes.length} koreksi validator</summary>
                          <ul className="list-disc pl-4 mt-1 space-y-0.5">
                            {autoPlan.fixes.map((f, i) => <li key={i}>{f}</li>)}
                          </ul>
                        </details>
                      )}
                    </>}
              </div>
            )}

            <div className="flex gap-3">
              <button
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-brand-600
                  hover:from-purple-500 hover:to-brand-500 text-white text-sm font-medium transition-all disabled:opacity-60"
                onClick={startAutoEdit}
                disabled={rendering && !isDone}
                title="Gemini menyusun rencana edit lalu langsung render"
              >
                <Wand2 size={16} /> Auto Edit
              </button>
              <button
                className="btn-primary flex-1 justify-center"
                onClick={startRender}
                disabled={rendering && !isDone}
              >
                {rendering && !isDone
                  ? <Loader2 size={16} className="animate-spin" />
                  : <Play size={16} />
                }
                {rendering && !isDone ? "Rendering…" : "Render"}
              </button>

              {isDone && renderId && (
                <a
                  href={`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/editor/render/${renderId}/file`}
                  className="btn-primary flex items-center gap-2"
                  download
                >
                  <Download size={16} /> Download
                </a>
              )}
            </div>
          </div>

          {isDone && renderId && <UploadPanel renderId={renderId} />}
        </>
      )}
    </div>
  )
}
