"use client"

import { useState, useEffect } from 'react'
import { useTheme } from 'next-themes'
import ChatInterface from '@/components/chat/ChatInterface'
import Sidebar from '@/components/sidebar/sidebar'
import { ModeToggle } from '@/components/theme-toggle'
import { Settings } from 'lucide-react'

export default function Home() {
  const [mounted, setMounted] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false) // Start with sidebar closed
  const { theme } = useTheme()

  // Prevent hydration mismatch
  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return null
  }

  return (
    <main className="flex h-screen overflow-hidden">
      {/* Sidebar - overlay style when open */}
      {sidebarOpen && (
        <>
          {/* Backdrop */}
          <div 
            className="fixed inset-0 bg-black/50 z-40"
            onClick={() => setSidebarOpen(false)}
          />
          
          {/* Sidebar */}
          <div className="fixed left-0 top-0 h-full w-80 z-50 border-r border-border bg-card shadow-xl">
            <Sidebar onClose={() => setSidebarOpen(false)} />
          </div>
        </>
      )}

      {/* Main content - full width */}
      <div className="flex-1 flex flex-col h-full">
        {/* Header */}
        <header className="h-14 border-b border-border flex items-center justify-between px-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div className="flex items-center gap-3">
            <button 
              onClick={() => setSidebarOpen(true)}
              className="p-2 rounded-md hover:bg-secondary transition-colors"
              title="Open Settings"
            >
              <Settings className="h-5 w-5" />
            </button>
            <h1 className="text-lg font-semibold">Strands Agent with MCP</h1>
          </div>
          <div className="flex items-center gap-2">
            <ModeToggle />
          </div>
        </header>

        {/* Chat interface - full width */}
        <div className="flex-1 overflow-hidden">
          <ChatInterface />
        </div>
      </div>
    </main>
  )
}
