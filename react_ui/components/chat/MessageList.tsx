'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useStore } from '@/lib/store';
import { ChatMessage } from './ChatMessage';

interface MessageListProps {
  isLoading?: boolean;
  isRunning?: boolean;
}

export function MessageList({ isLoading = false,isRunning = false }: MessageListProps) {
  const { messages } = useStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isUserScrolling, setIsUserScrolling] = useState(false);
  const [lastMessageCount, setLastMessageCount] = useState(0);
  const scrollTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastScrollTimeRef = useRef<number>(0);
  
  // Check if user is near bottom of scroll area
  const isNearBottom = () => {
    if (!scrollContainerRef.current) return true;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
    return scrollHeight - scrollTop - clientHeight < 100; // Within 100px of bottom
  };
  
  // Handle scroll events to detect user scrolling with debouncing
  const handleScroll = useCallback(() => {
    if (!scrollContainerRef.current) return;
    
    const now = Date.now();
    lastScrollTimeRef.current = now;
    
    // Clear existing timeout
    if (scrollTimeoutRef.current) {
      clearTimeout(scrollTimeoutRef.current);
    }
    
    // Set user scrolling state immediately
    const isAtBottom = isNearBottom();
    setIsUserScrolling(!isAtBottom);
    
    // Reset user scrolling state after a delay if they're at bottom
    if (isAtBottom) {
      scrollTimeoutRef.current = setTimeout(() => {
        // Only reset if no recent scroll activity
        if (Date.now() - lastScrollTimeRef.current > 500) {
          setIsUserScrolling(false);
        }
      }, 1000);
    }
  }, []);
  
  // Auto-scroll logic with improved handling for streaming
  useEffect(() => {
    const messageCount = messages.length;
    const hasNewMessage = messageCount > lastMessageCount;
    
    // For streaming updates (content changes without new messages)
    const shouldAutoScrollForStreaming = isRunning && !isUserScrolling && isNearBottom();
    
    // For new messages
    const shouldAutoScrollForNewMessage = hasNewMessage && (!isUserScrolling || isNearBottom());
    
    if (shouldAutoScrollForNewMessage || shouldAutoScrollForStreaming) {
      // Use requestAnimationFrame to ensure DOM is updated
      requestAnimationFrame(() => {
        if (messagesEndRef.current) {
          messagesEndRef.current.scrollIntoView({
            behavior: isRunning ? 'auto' : 'smooth' // Use instant scroll during streaming
          });
        }
      });
    }
    
    if (hasNewMessage) {
      setLastMessageCount(messageCount);
    }
  }, [messages, isUserScrolling, isRunning, lastMessageCount]);
  
  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current);
      }
    };
  }, []);
  
  // Filter out system messages for display
  const displayMessages = messages.filter(msg => msg.role !== 'system');
  
  return (
    <div
      ref={scrollContainerRef}
      className="flex-1 overflow-y-auto p-4 space-y-4"
      onScroll={handleScroll}
    >
      {isLoading ? (
        <div className="flex items-center justify-center h-full">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-4 border-gray-300 border-t-blue-600 mb-4"></div>
            <h3 className="text-lg font-medium">Preparing...</h3>
            <p className="text-sm text-gray-500 mt-2">
              Please wait while we connect to available MCP servers.
            </p>
          </div>
        </div>
      ) : displayMessages.length === 0 ? (
        <div className="flex items-center justify-center h-full">
          <div className="text-center">
            <h3 className="text-lg font-medium">Welcome to Autonomous Agent with MCP</h3>
            <p className="text-sm text-gray-500 mt-2">
              Start a conversation by typing a message below.
            </p>
          </div>
        </div>
      ) : (
        displayMessages.map((message, index) => {
          const isLastMessage = index === displayMessages.length - 1;
          const shouldShowRunning = isRunning && isLastMessage;
          
          return (
            <ChatMessage
              key={index}
              message={message}
              isRunning={shouldShowRunning}
            />
          );
        })
      )}
      <div ref={messagesEndRef} />
    </div>
  );
}
