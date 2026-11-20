import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { Layers, Type, Star, Sparkles } from "lucide-react"
import { api } from "../lib/api"
import { SfxSection } from "../components/assets/SfxSection"
import { AudioImportSection } from "../components/assets/AudioImportSection"

type Style = {
  id: string; name: string; created_by: string; favorite: boolean; notes: string
  recipe: { font: { family: string; weight: string }; fill_colors: string[]; words_per_line: number; animation: { type: string } }
}

export function AssetsPage() {
  const [styles, setStyles] = useState<Style[]>([])
  const [sfxKey, setSfxKey] = useState(0)
  useEffect(() => { api.get("/api/studio/styles").then(r => setStyles(r.data)) }, [])

  return (
    <div className="max-w-4xl mx-auto py-10 px-4 space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Layers size={24} className="text-brand-500" /> Assets
      </h1>

      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-sm uppercase tracking-wide text-zinc-400 flex items-center gap-2">
            <Type size={14} /> Gaya Subtitle
          </h2>
          <Link to="/studio" className="btn-primary text-sm">Buka Studio Subtitle</Link>
        </div>
        <p className="text-xs text-zinc-500">
          Tidak ada template tetap. Gaya dirancang di Studio (oleh Anda atau Gemini); gaya favorit dipakai Gemini sebagai titik awal saat Auto Edit.
        </p>
        {styles.length === 0 && <p className="text-sm text-zinc-500">Belum ada gaya.</p>}
        <div className="grid sm:grid-cols-2 gap-2">
          {styles.map(s => (
            <Link key={s.id} to={`/studio?style=${s.id}`}
              className="border border-zinc-800 hover:border-zinc-600 rounded-lg p-3 text-sm space-y-1">
              <div className="flex items-center gap-2">
                {s.favorite && <Star size={13} className="text-yellow-400" />}
                {s.created_by === "gemini" && <Sparkles size={13} className="text-purple-400" />}
                <span className="text-zinc-200">{s.name}</span>
              </div>
              <div className="flex items-center gap-1">
                {s.recipe.fill_colors.map((c, i) => (
                  <span key={i} className="w-4 h-4 rounded border border-zinc-700" style={{ background: c }} />
                ))}
                <span className="text-xs text-zinc-500 ml-2">
                  {s.recipe.font.family} {s.recipe.font.weight} · {s.recipe.words_per_line} kata · {s.recipe.animation.type}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <AudioImportSection onImported={() => setSfxKey(k => k + 1)} />
      <SfxSection refreshKey={sfxKey} />
    </div>
  )
}
