'use client';

import { useState, useEffect, useRef } from 'react';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { useStore } from '@/lib/store';
import { v4 as uuidv4 } from 'uuid';
import { fetchMcpServers } from '@/lib/api/chat';

export default function ChatInterface() {
  const [isLoadingMcpServers, setIsLoadingMcpServers] = useState(true);
  const loadedRef = useRef<boolean>(false);
  const { userId, setUserId, mcpServers, setMcpServers } = useStore();
  
  // Initialize userId if not set
  useEffect(() => {
    if (!userId) {
      // Check if user ID exists in localStorage
      const storedUserId = localStorage.getItem('mcp_chat_user_id');
      if (storedUserId) {
        setUserId(storedUserId);
      } else {
        // Generate new random user ID
        const newUserId = uuidv4().substring(0, 8);
        setUserId(newUserId);
        localStorage.setItem('mcp_chat_user_id', newUserId);
      }
    }
  }, [userId, setUserId]);
  
  // Fetch MCP servers when component mounts - only run once
  useEffect(() => {
    // Only load servers once
    if (loadedRef.current) return;
    
    const loadMcpServers = async () => {
      setIsLoadingMcpServers(true);
      try {
        const servers = await fetchMcpServers();
        
        // Process servers
        let updatedServers;
        if (mcpServers.length > 0) {
          // Preserve enabled state from existing servers
          updatedServers = servers.map(newServer => {
            const existingServer = mcpServers.find(
              existing => existing.serverId === newServer.serverId
            );
            
            if (existingServer) {
              return {
                ...newServer,
                enabled: existingServer.enabled
              };
            }
            
            return newServer;
          });
        } else {
          updatedServers = servers;
        }
        
        // Update the store
        setMcpServers(updatedServers);
        
        // Mark as loaded
        loadedRef.current = true;
      } catch (error) {
        console.error('Failed to load MCP servers:', error);
      } finally {
        setIsLoadingMcpServers(false);
      }
    };
    
    loadMcpServers();
  }, []); // Empty dependency array to run only once
  
  return (
    <div className="flex flex-col h-full">
      {/* Main chat area - full width */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-col w-full min-w-0">
          {/* Message list */}
          <MessageList isLoading={isLoadingMcpServers} />
          
          {/* Chat input */}
          <ChatInput disabled={isLoadingMcpServers} />
        </div>
      </div>
    </div>
  );
}
