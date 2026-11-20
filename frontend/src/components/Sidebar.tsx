import { NavLink } from "react-router-dom"
import {
  Scissors, History, Download, Tv2, Radar, Camera,
  Settings, Clapperboard, Layers, GraduationCap, Type,
} from "lucide-react"

const NAV = [
  { to: "/",          icon: Scissors,    label: "Analyze" },
  { to: "/history",   icon: History,     label: "History" },
  { to: "/download",  icon: Download,    label: "Download" },
  { to: "/youtube",   icon: Tv2,         label: "YouTube" },
  { to: "/scout",     icon: Radar,       label: "Scout" },
  { to: "/meta",      icon: Camera,      label: "Meta" },
  { to: "/editor",    icon: Clapperboard,label: "Editor" },
  { to: "/studio",    icon: Type,        label: "Studio" },
  { to: "/learning",  icon: GraduationCap, label: "Learning" },
  { to: "/assets",    icon: Layers,      label: "Assets" },
  { to: "/settings",  icon: Settings,    label: "Settings" },
]

export function Sidebar() {
  return (
    <aside className="w-16 lg:w-52 flex flex-col bg-zinc-900 border-r border-zinc-800 h-screen sticky top-0 shrink-0">
      {/* logo */}
      <div className="flex items-center gap-2 px-4 py-5 border-b border-zinc-800">
        <span className="text-brand-500 font-bold text-xl">✂</span>
        <span className="hidden lg:block font-semibold text-zinc-100">Clipodify</span>
      </div>

      <nav className="flex-1 overflow-y-auto py-3">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg text-sm transition-colors
               ${isActive
                 ? "bg-brand-600/20 text-brand-400 font-medium"
                 : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800"
               }`
            }
          >
            <Icon size={18} className="shrink-0" />
            <span className="hidden lg:block">{label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
