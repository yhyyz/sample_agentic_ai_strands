"""
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
"""
"""
Strands Agents SDK based streaming chat client
"""
import os
import sys
import asyncio
import logging
import json
import time
import threading
from typing import Dict, AsyncGenerator, Optional, List, AsyncIterator, Any
from dotenv import load_dotenv
from strands_agent_client import StrandsAgentClient
from mcp_client_strands import StrandsMCPClient
from utils import maybe_filter_to_n_most_recent_images, remove_cache_checkpoint,get_stream_id
from constant import *
load_dotenv()  # load environment variables from .env

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
)
logger = logging.getLogger(__name__)

class StrandsAgentClientStream(StrandsAgentClient):
    """Extended Strands Agent Client with streaming support"""
    
    def __init__(self, credential_file='', user_id='',model_provider='bedrock', api_key='', api_base=None, 
                 access_key_id='', secret_access_key='', region=''):
        super().__init__(credential_file, user_id,access_key_id, secret_access_key, region,
                        model_provider, api_key, api_base)
        # Stream-specific properties
        self.stop_flags = {}  # Dict to track stop flags for streams
        self.monitor_threads = {}  # Dict to track monitor threads for streams
        self.thread_stop_events = {}  # Dict to track stop events for threads
        self.agent_threads = {}  # Dict to track agent processing threads
        self.agent_stop_events = {}  # Dict to track stop events for agent threads
        self.stream_queues = {}  # Dict to store stream results from agent threads
        
    def register_stream(self, stream_id):
        """Register a new stream with a stop flag"""
        self.stop_flags[stream_id] = False
        logger.info(f"Registered stream: {stream_id}")
    
    def stop_stream(self, stream_id):
        """Set the stop flag for a stream to terminate it"""
        if stream_id in self.stop_flags:
            self.stop_flags[stream_id] = True
            logger.info(f"Stopping stream: {stream_id}")
            return True
        logger.warning(f"Attempted to stop unknown stream: {stream_id}")
        return False

    def unregister_stream(self, stream_id):
        """Clean up the stop flag after a stream completes"""
        if stream_id in self.stop_flags:
            del self.stop_flags[stream_id]
            logger.info(f"Unregistered stream: {stream_id}")
        
        # Stop and clean up monitor thread
        self._stop_monitor_thread(stream_id)
            
    def _start_monitor_thread(self, stream_id: str):
        """Start a monitor thread for the given stream"""
        if stream_id in self.monitor_threads:
            logger.warning(f"Monitor thread for stream {stream_id} already exists")
            return
            
        # Create stop event for this thread
        stop_event = threading.Event()
        self.thread_stop_events[stream_id] = stop_event
        
        # Create and start monitor thread
        monitor_thread = threading.Thread(
            target=self._monitor_stream_status,
            args=(stream_id, stop_event),
            daemon=True,
            name=f"StreamMonitor-{stream_id}"
        )
        self.monitor_threads[stream_id] = monitor_thread
        monitor_thread.start()
        logger.info(f"Started monitor thread for stream: {stream_id}")
    
    def _stop_monitor_thread(self, stream_id: str):
        """Stop the monitor thread for the given stream"""
        if stream_id in self.thread_stop_events:
            self.thread_stop_events[stream_id].set()
            del self.thread_stop_events[stream_id]
            
        if stream_id in self.monitor_threads:
            monitor_thread = self.monitor_threads[stream_id]
            # Give thread a moment to stop gracefully
            monitor_thread.join(timeout=1.0)
            del self.monitor_threads[stream_id]
            logger.info(f"Stopped monitor thread for stream: {stream_id}")
    
    def _start_agent_thread(self, stream_id: str, prompt: str):
        """Start an agent processing thread for the given stream"""
        if stream_id in self.agent_threads:
            logger.warning(f"Agent thread for stream {stream_id} already exists")
            return
            
        # Create stop event for this thread
        stop_event = threading.Event()
        self.agent_stop_events[stream_id] = stop_event
        
        # Create queue for stream results
        import queue
        stream_queue = queue.Queue()
        self.stream_queues[stream_id] = stream_queue
        
        # Create and start agent thread
        agent_thread = threading.Thread(
            target=self._run_agent_stream,
            args=(stream_id, prompt, stop_event, stream_queue),
            daemon=True,
            name=f"AgentStream-{stream_id}"
        )
        self.agent_threads[stream_id] = agent_thread
        agent_thread.start()
        logger.info(f"Started agent thread for stream: {stream_id}")
    
    def _stop_agent_thread(self, stream_id: str):
        """Stop the agent thread for the given stream"""
        if stream_id in self.agent_stop_events:
            self.agent_stop_events[stream_id].set()
            del self.agent_stop_events[stream_id]
            
        if stream_id in self.agent_threads:
            agent_thread = self.agent_threads[stream_id]
            
            # First, try graceful shutdown
            agent_thread.join(timeout=2.0)
            
            # If thread is still alive, it means there might be daemon threads or blocking operations
            if agent_thread.is_alive():
                logger.warning(f"Agent thread for stream {stream_id} did not stop gracefully, forcing cleanup")
                
                # Try to get thread ID for more aggressive cleanup if needed
                thread_id = agent_thread.ident
                if thread_id:
                    logger.info(f"Agent thread {thread_id} for stream {stream_id} is still running")
                
                # Note: In Python, we cannot forcefully kill threads, but we can:
                # 1. Set stop events (already done)
                # 2. Clear references to allow garbage collection
                # 3. Log the situation for monitoring
                
                # The daemon threads will be terminated when the main process exits
                # But we should clean up our references
                logger.warning(f"Agent thread for stream {stream_id} may have daemon threads still running")
            
            del self.agent_threads[stream_id]
            logger.info(f"Stopped agent thread for stream: {stream_id}")
            
        if stream_id in self.stream_queues:
            # Clear the queue to free memory
            stream_queue = self.stream_queues[stream_id]
            try:
                while not stream_queue.empty():
                    stream_queue.get_nowait()
            except:
                pass  # Queue might be empty or have other issues
            del self.stream_queues[stream_id]
    
    def _run_agent_stream(self, stream_id: str, prompt: str, stop_event: threading.Event, stream_queue):
        """Run agent stream processing in a separate thread"""
        logger.info(f"Agent thread started for stream: {stream_id}")
        
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run the agent stream processing
            loop.run_until_complete(self._agent_stream_worker(stream_id, prompt, stop_event, stream_queue))
            
        except Exception as e:
            logger.error(f"Error in agent thread for stream {stream_id}: {e}")
            # Put error in queue
            stream_queue.put({"type": "error", "data": {"message": str(e)}})
        finally:
            logger.info(f"Agent thread for stream {stream_id} terminated")
    
    async def _agent_stream_worker(self, stream_id: str, prompt: str, stop_event: threading.Event, stream_queue):
        """Async worker for agent stream processing"""
        try:
            if not self.agent:
                logger.error(f"No agent available for stream {stream_id}")
                return
                
            response = self.agent.stream_async(prompt)
            async for event in self._process_stream_response(stream_id, response):
                if stop_event.is_set():
                    logger.info(f"Agent stream worker for {stream_id} stopped by event")
                    break
                    
                # Put event in queue for main thread to consume
                stream_queue.put(event)
                
            # Signal end of stream
            stream_queue.put({"type": "stream_end"})
            
        except Exception as e:
            logger.error(f"Error in agent stream worker for {stream_id}: {e}")
            stream_queue.put({"type": "error", "data": {"message": str(e)}})
    
    def _monitor_stream_status(self, stream_id: str, stop_event: threading.Event):
        """Monitor stream status in a separate thread"""
        logger.info(f"Monitor thread started for stream: {stream_id}")
        
        try:
            while not stop_event.is_set():
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Check if stream still exists in remote DDB
                    stream_exists = loop.run_until_complete(get_stream_id(stream_id=stream_id))
                    
                    # If stream doesn't exist and agent exists, clean up
                    if not stream_exists and hasattr(self, 'agent') and self.agent:
                        logger.info(f"Stream {stream_id} not found in remote, cleaning up agent")
                        # Set stop flag to terminate the stream
                        if stream_id in self.stop_flags:
                            self.stop_flags[stream_id] = True
                        # Clean up agent
                        # del self.agent
                        # self.agent = None
                        break
                        
                    loop.close()
                    
                except Exception as e:
                    logger.error(f"Error in monitor thread for stream {stream_id}: {e}")
                
                # Wait for 2 seconds before next check, but allow early termination
                stop_event.wait(timeout=2.0)
                
        except Exception as e:
            logger.error(f"Monitor thread for stream {stream_id} encountered error: {e}")
        finally:
            logger.info(f"Monitor thread for stream {stream_id} terminated")
    
    async def _process_stream_response(self, stream_id: Optional[str], response) -> AsyncIterator[Dict]:
        """Process the raw response from converse_stream"""
        last_yield_time = time.time()
        async for chunk in response:
            current_time = time.time()
            if current_time - last_yield_time > 0.1:  # 每100ms让出一次控制权，避免阻塞
                await asyncio.sleep(0.001)
                last_yield_time = current_time
            # Check if we need to stop
            if stream_id and stream_id in self.stop_flags and self.stop_flags[stream_id]:
                logger.info(f"Stream {stream_id} was requested to stop")
                yield {"type": "stopped", "data": {"message": "Stream stopped by user request"}}
                break
            if 'message' in chunk:
                message = chunk['message']
                if message.get('role') == 'user' and message.get('content'):
                    content = message['content']
                    for content_block in content:
                        if 'toolResult' in content_block:
                            toolUseId = content_block['toolResult']['toolUseId']
                            yield {"type": "toolResult", "toolUseId":toolUseId,"data": content_block['toolResult']}
                    
            elif 'event' in chunk:
                event = chunk['event']
                # logger.info(event)
                # Handle message start
                if "messageStart" in event:
                    yield {"type": "message_start", "data": event["messageStart"]}
                    continue

                # Handle content block start
                if "contentBlockStart" in event:
                    block_start = event["contentBlockStart"]
                    yield {"type": "block_start", "data": block_start}
                    continue 

                # Handle content block delta
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"]
                    yield {"type": "block_delta", "data": delta}
                    continue

                # Handle content block stop
                if "contentBlockStop" in event:
                    yield {"type": "block_stop", "data": event["contentBlockStop"]}
                    continue

                # Handle message stop
                if "messageStop" in event:
                    yield {"type": "message_stop", "data": event["messageStop"]}
                    continue

                # Handle metadata
                if "metadata" in event:
                    yield {"type": "metadata", "data": event["metadata"]}
                    continue
            
    async def process_query_stream(self,
            model_id="", max_tokens=1024, max_turns=30, temperature=0.1,
            messages=[], system=[], mcp_clients=None, mcp_server_ids=[], extra_params={}, keep_session=None,
            stream_id: Optional[str] = None) -> AsyncGenerator[Dict, None]:
        """Submit user query or history messages, and get streaming response using Strands Agents SDK."""
        
        logger.info(f'client input message list length:{len(messages)}')
        if not messages:
            raise ValueError('empty message')
        # must be kept with Strands
        keep_session = True
        if keep_session:
            history = await self.load_history()
            if history:
                messages = history + messages 
            system = self.system if self.system else system #system 消息每次都会传入
        else:
            await self.clear_history()
            
        logger.info(f'llm input message list length:{len(messages)}')
        
        # Register this stream if an ID is provided
        if stream_id:
            self.register_stream(stream_id)
            # Start monitor thread for this stream
            self._start_monitor_thread(stream_id)
        
        # Convert system messages to system prompt
        system_prompt = ""
        if system:
            for item in system:
                if isinstance(item, dict) and "text" in item:
                    system_prompt += item["text"]
        
        # 添加用户id标志，用于mem0
        user_identity = f"\nHere is the request from User with user id:{self.user_id}\n"
        system_prompt += user_identity
        # Convert messages to Strands format
        # strands_messages = self._convert_messages_to_strands_format(messages)
        history_messages = messages[:-1]
        prompt = messages[-1]['content'][0]['text']
        thinking = extra_params.get('enable_thinking', False) and model_id in [CLAUDE_37_SONNET_MODEL_ID,CLAUDE_4_SONNET_MODEL_ID,CLAUDE_4_OPUS_MODEL_ID]
        thinking_budget = extra_params.get("budget_tokens",4096)
        max_tokens = max(thinking_budget + 1, max_tokens) if thinking else max_tokens
        # Create agent with MCP tools
        self.agent = await self._create_agent_with_tools(
            messages=history_messages,
            model_id=model_id,
            mcp_clients=mcp_clients,
            mcp_server_ids=mcp_server_ids,
            system_prompt=system_prompt,
            thinking=thinking,
            thinking_budget=thinking_budget,
            max_tokens=max_tokens,
            temperature=temperature
        )
        
        current_content = ""
        turn_i = 1
        stop_reason = ''
        tool_calls = []
        current_tool_use = None
        current_tooluse_input = ''
        thinking_text = ''
        text = ''
        only_n_most_recent_images = extra_params.get('only_n_most_recent_images', 3)
        image_truncation_threshold = only_n_most_recent_images or 0
        
        tool_calls = []
        tool_results_dict = {}
        # 记录已经发送过的tool result
        sent_results_history = {}
        # Check if stream_id is provided
        if not stream_id:
            yield {"type": "error", "data": {"message": "无stream id"}}
            return
            
        # Start agent thread to handle stream processing
        self._start_agent_thread(stream_id, prompt)
        
        # Get events from agent thread via queue
        import queue
        stream_queue = self.stream_queues[stream_id]
        
        while True:
            try:
                # Get event from queue with timeout
                event = stream_queue.get(timeout=1.0)
                
                # Check if stream should stop
                if stream_id in self.stop_flags and self.stop_flags[stream_id]:
                    logger.info(f"Stream {stream_id} was requested to stop")
                    yield {"type": "stopped", "data": {"message": "Stream stopped by user request"}}
                    break
                
                # Handle special control events
                if event.get("type") == "stream_end":
                    logger.info(f"Stream {stream_id} ended normally")
                    break
                elif event.get("type") == "error":
                    logger.error(f"Stream {stream_id} encountered error: {event.get('data', {}).get('message', 'Unknown error')}")
                    yield event
                    break
                
                # Yield normal events
                yield event
                
            except queue.Empty:
                # Check if we should continue waiting
                if stream_id in self.stop_flags and self.stop_flags[stream_id]:
                    logger.info(f"Stream {stream_id} timed out and stop flag is set")
                    break
                continue
            except Exception as e:
                logger.error(f"Error getting event from queue for stream {stream_id}: {e}")
                break
            # Handle tool use in content block start
            if event["type"] == "block_start":
                block_start = event["data"]
                if "toolUse" in block_start.get("start", {}):
                    current_tool_use = block_start["start"]["toolUse"]
                    tool_calls.append(current_tool_use)
                    logger.info("Tool use detected: %s", current_tool_use)

            if event["type"] == "block_delta":
                delta = event["data"]
                if "toolUse" in delta.get("delta", {}):
                    #Claude 是stream输出input，而Nova是一次性输出
                    #取出最近添加的tool,追加input参数
                    current_tool_use = tool_calls[-1]
                    if current_tool_use:
                        current_tooluse_input += delta["delta"]["toolUse"]["input"]
                        current_tool_use["input"] = current_tooluse_input 
                    
            # Handle tool use input in content block stop
            if event["type"] == "block_stop":
                if current_tooluse_input:
                    #取出最近添加的tool,把input str转成json
                    current_tool_use = tool_calls[-1]
                    if current_tool_use:
                        current_tool_use["input"] = json.loads(current_tooluse_input)
                        current_tooluse_input = ''
                        
                        
            # Handle message stop and tool use
            if event["type"] == "toolResult":
                new_event = {}
                toolUseId = event['toolUseId']
                if toolUseId not in tool_results_dict:
                    tool_results_dict[toolUseId] = event['data']
                    # output tool results for UI
                    tool_results_serializable = [[tool,{"tool_name":tool['name'],"tool_result":tool_results_dict.get(tool['toolUseId'])}] for tool in tool_calls 
                                                    if tool_results_dict.get(tool['toolUseId']) and tool['toolUseId'] not in sent_results_history ]
                    # tool_results = [item for pair in zip(tool_calls, tool_results_serializable) for item in pair]
                    tool_results = [item for pair in tool_results_serializable for item in pair]
                    new_event = {'type':'result_pairs','data':{'stopReason':'tool_use','tool_results':tool_results}}
                    yield new_event
                    sent_results_history[toolUseId] = toolUseId
                    # logger.info(new_event)
                
            if event["type"] == "message_stop":
                # Save the system to session
                self.system = system
                yield event
        
        # Clean up after stream completes
        if stream_id:
            self.unregister_stream(stream_id)