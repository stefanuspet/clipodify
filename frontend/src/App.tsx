import { BrowserRouter, Routes, Route } from "react-router-dom"
import { Sidebar } from "./components/Sidebar"
import { AnalyzePage }  from "./pages/AnalyzePage"
import { HistoryPage }  from "./pages/HistoryPage"
import { DownloadPage } from "./pages/DownloadPage"
import { YouTubePage }  from "./pages/YouTubePage"
import { ScoutPage }    from "./pages/ScoutPage"
import { MetaPage }     from "./pages/MetaPage"
import { EditorPage }   from "./pages/EditorPage"
import { SettingsPage } from "./pages/SettingsPage"
import { AssetsPage }   from "./pages/AssetsPage"

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/"         element={<AnalyzePage />} />
            <Route path="/history"  element={<HistoryPage />} />
            <Route path="/download" element={<DownloadPage />} />
            <Route path="/youtube"  element={<YouTubePage />} />
            <Route path="/scout"    element={<ScoutPage />} />
            <Route path="/meta"     element={<MetaPage />} />
            <Route path="/editor"   element={<EditorPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/assets"   element={<AssetsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
