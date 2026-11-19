interface Props {
  step: number
  total: number
  label: string
  detail?: string
}

export function ProgressBar({ step, total, label, detail }: Props) {
  const pct = total > 0 ? Math.round((step / total) * 100) : 0
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs text-zinc-400">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
        <div
          className="h-full bg-brand-600 rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      {detail && <p className="text-xs text-zinc-500">{detail}</p>}
    </div>
  )
}
