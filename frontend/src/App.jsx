import { useState, useEffect } from "react"
import WebApp from "@twa-dev/sdk"
import "./App.css"
import Dashboard from "./tabs/Dashboard"
import Services from "./tabs/Services"
import OTPTab from "./tabs/OTPTab"
import History from "./tabs/History"

const TABS = [
  {
    id: "dashboard", label: "Trang chủ",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>
        <polyline points="9 22 9 12 15 12 15 22"/>
      </svg>
    ),
  },
  {
    id: "services", label: "Thuê số",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>
      </svg>
    ),
  },
  {
    id: "otp", label: "OTP",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/>
      </svg>
    ),
  },
  {
    id: "history", label: "Lịch sử",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
      </svg>
    ),
  },
]

export default function App() {
  const [tab, setTab] = useState("dashboard")
  const [pendingRequestId, setPendingRequestId] = useState(null)

  useEffect(() => {
    try {
      WebApp.ready()
      WebApp.expand()
    } catch {}
  }, [])

  const handleBought = (requestId) => {
    setPendingRequestId(requestId)
    setTab("otp")
  }

  return (
    <div className="app">
      <div className="content">
        {tab === "dashboard" && <Dashboard />}
        {tab === "services" && <Services onBought={handleBought} />}
        {tab === "otp" && <OTPTab pendingRequestId={pendingRequestId} />}
        {tab === "history" && <History />}
      </div>

      <nav className="tab-bar">
        {TABS.map(t => (
          <button
            key={t.id}
            className={"tab-btn" + (tab === t.id ? " active" : "")}
            onClick={() => {
              if (t.id !== "otp") setPendingRequestId(null)
              setTab(t.id)
            }}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  )
}
