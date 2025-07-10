import { ChatProvider } from '@/components/providers/ChatProvider'

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <ChatProvider>
      {children}
    </ChatProvider>
  )
}
