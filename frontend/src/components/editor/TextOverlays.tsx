import { Plus, Trash2, Type } from "lucide-react"

// ---------------------------------------------------------------- types

export interface TextOverlay {
  id:       string
  kind:     string
  text:     string
  start:    number
  duration: number
  position: "top" | "center" | "bottom"
  color:    string
  font:     string
}

interface Props {
  overlays:    TextOverlay[]
  clipDuration: number          // seconds — for slider max
  onChange:    (overlays: TextOverlay[]) => void
}

// ---------------------------------------------------------------- constants

const FONTS = [
  { value: "Anton",             label: "Anton"         },
  { value: "BebasNeue",         label: "Bebas Neue"    },
  { value: "Montserrat-Bold",   label: "Montserrat"    },
  { value: "Poppins-Bold",      label: "Poppins"       },
]

const POSITIONS = [
  { value: "top",    label: "Atas"   },
  { value: "center", label: "Tengah" },
  { value: "bottom", label: "Bawah"  },
]

const PRESET_COLORS = [
  "#FFFFFF", "#FFFF00", "#FF4444", "#44FF88", "#44AAFF", "#FF8800",
]

// ---------------------------------------------------------------- single row

function OverlayRow({
  overlay, clipDuration, onUpdate, onRemove,
}: {
  overlay: TextOverlay
  clipDuration: number
  onUpdate: (patch: Partial<TextOverlay>) => void
  onRemove: () => void
}) {
  const maxStart = Math.max(0, clipDuration - 0.5)

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 space-y-3">
      {/* Text input + remove */}
      <div className="flex items-center gap-2">
        <input
          className="input flex-1 text-sm"
          placeholder="Teks overlay…"
          value={overlay.text}
          onChange={e => onUpdate({ text: e.target.value })}
        />
        <button
          onClick={onRemove}
          className="text-zinc-600 hover:text-red-400 transition-colors shrink-0"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Position + Font */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label className="text-zinc-500 text-xs">Posisi</label>
          <div className="flex gap-1">
            {POSITIONS.map(p => (
              <button
                key={p.value}
                type="button"
                onClick={() => onUpdate({ position: p.value as TextOverlay["position"] })}
                className={`flex-1 py-1 rounded-lg text-xs border transition-colors
                  ${overlay.position === p.value
                    ? "border-brand-500 bg-brand-600/10 text-brand-400"
                    : "border-zinc-700 text-zinc-400 hover:border-zinc-600"}`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-zinc-500 text-xs">Font</label>
          <select
            value={overlay.font}
            onChange={e => onUpdate({ font: e.target.value })}
            className="input text-xs py-1.5"
          >
            {FONTS.map(f => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Color swatches */}
      <div className="space-y-1.5">
        <label className="text-zinc-500 text-xs">Warna</label>
        <div className="flex items-center gap-2">
          {PRESET_COLORS.map(c => (
            <button
              key={c}
              type="button"
              onClick={() => onUpdate({ color: c })}
              className={`w-6 h-6 rounded-full border-2 transition-transform
                ${overlay.color === c ? "border-white scale-110" : "border-transparent"}`}
              style={{ backgroundColor: c }}
            />
          ))}
          {/* Custom color picker */}
          <input
            type="color"
            value={overlay.color}
            onChange={e => onUpdate({ color: e.target.value })}
            className="w-6 h-6 rounded-full cursor-pointer bg-transparent border-0 p-0"
            title="Warna custom"
          />
          <span className="text-zinc-600 text-xs font-mono ml-1">{overlay.color}</span>
        </div>
      </div>

      {/* Start + Duration sliders */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-zinc-500 text-xs flex justify-between">
            Mulai <span className="text-zinc-300">{overlay.start.toFixed(1)}s</span>
          </label>
          <input
            type="range" min="0" max={maxStart} step="0.5"
            value={overlay.start}
            onChange={e => {
              const v = parseFloat(e.target.value)
              onUpdate({ start: v })
            }}
            className="w-full accent-brand-500"
          />
        </div>
        <div className="space-y-1">
          <label className="text-zinc-500 text-xs flex justify-between">
            Durasi <span className="text-zinc-300">{overlay.duration.toFixed(1)}s</span>
          </label>
          <input
            type="range" min="0.5" max={Math.min(10, clipDuration)} step="0.5"
            value={overlay.duration}
            onChange={e => onUpdate({ duration: parseFloat(e.target.value) })}
            className="w-full accent-brand-500"
          />
        </div>
      </div>

      {/* Preview text */}
      {overlay.text && (
        <div className={`text-center py-1 text-sm font-bold tracking-wide
          ${overlay.position === "top" ? "border-t" : overlay.position === "center" ? "border-y" : "border-b"}
          border-zinc-800`}
          style={{ color: overlay.color, fontFamily: overlay.font.replace(/-\w+$/, "") }}
        >
          {overlay.text}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- main component

export function TextOverlays({ overlays, clipDuration, onChange }: Props) {
  function add() {
    onChange([...overlays, {
      id:       crypto.randomUUID(),
      kind:     "custom",
      text:     "",
      start:    0,
      duration: 3,
      position: "bottom",
      color:    "#FFFFFF",
      font:     "Anton",
    }])
  }

  function update(id: string, patch: Partial<TextOverlay>) {
    onChange(overlays.map(o => o.id === id ? { ...o, ...patch } : o))
  }

  function remove(id: string) {
    onChange(overlays.filter(o => o.id !== id))
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
          <Type size={14} /> Text Overlays
        </h2>
        <button onClick={add} className="btn-ghost text-xs gap-1">
          <Plus size={13} /> Tambah
        </button>
      </div>

      {overlays.length === 0 ? (
        <p className="text-zinc-600 text-xs text-center py-3">
          Tambah teks yang muncul di video — cocok untuk hook, kalimat kuat, atau CTA
        </p>
      ) : (
        <div className="space-y-3">
          {overlays.map(o => (
            <OverlayRow
              key={o.id}
              overlay={o}
              clipDuration={clipDuration}
              onUpdate={patch => update(o.id, patch)}
              onRemove={() => remove(o.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
