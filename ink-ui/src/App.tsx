import React, { useState, useEffect, useCallback } from 'react';
import { Box, Text, useApp } from 'ink';
import { Banner } from './components/Banner.js';
import { ChatLog, ChatEntry } from './components/ChatLog.js';
import { InputBar } from './components/InputBar.js';
import { ThinkingSpinner } from './components/ThinkingSpinner.js';
import { PermissionDialog } from './components/PermissionDialog.js';
import type { BackendMessage, FrontendMessage } from './protocol.js';
import * as readline from 'readline';

function sendToBackend(msg: FrontendMessage) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

export function App() {
  const { exit } = useApp();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [currentText, setCurrentText] = useState('');
  const [welcomeData, setWelcomeData] = useState<{
    model: string;
    workspace: string;
    cwd: string;
    permissions: string;
    branch: string;
  } | null>(null);
  const [permissionRequest, setPermissionRequest] = useState<{
    toolName: string;
    args: string;
  } | null>(null);

  const addEntry = useCallback((entry: ChatEntry) => {
    setEntries(prev => [...prev, entry]);
  }, []);

  const handleBackendMessage = useCallback((msg: BackendMessage) => {
    switch (msg.type) {
      case 'welcome':
        setWelcomeData({
          model: msg.model,
          workspace: msg.workspace,
          cwd: msg.cwd,
          permissions: msg.permissions,
          branch: msg.branch,
        });
        break;
      case 'user_echo':
        addEntry({ type: 'user', text: msg.text });
        break;
      case 'thinking_start':
        setIsThinking(true);
        break;
      case 'thinking_stop':
        setIsThinking(false);
        break;
      case 'text_delta':
        setCurrentText(prev => prev + msg.text);
        break;
      case 'text_done':
        if (msg.text) {
          addEntry({ type: 'assistant', text: msg.text });
        }
        setCurrentText('');
        break;
      case 'tool_start':
        addEntry({ type: 'tool_start', name: msg.name, detail: msg.detail });
        break;
      case 'tool_result':
        addEntry({ type: 'tool_result', name: msg.name, output: msg.output, isError: msg.isError });
        break;
      case 'turn_done':
        addEntry({ type: 'status', text: `✓ Done (${msg.elapsed.toFixed(1)}s)  ↓${msg.tokens} tok` });
        break;
      case 'permission_request':
        setPermissionRequest({ toolName: msg.toolName, args: msg.args });
        break;
      case 'message':
        addEntry({ type: 'info', text: msg.text, style: msg.style });
        break;
      case 'error':
        addEntry({ type: 'error', text: msg.message });
        break;
      case 'help':
        addEntry({ type: 'help', commands: msg.commands });
        break;
    }
  }, [addEntry]);

  // Read JSON lines from stdin (Python backend)
  useEffect(() => {
    const rl = readline.createInterface({ input: process.stdin });
    rl.on('line', (line: string) => {
      try {
        const msg: BackendMessage = JSON.parse(line);
        handleBackendMessage(msg);
      } catch {
        // ignore malformed lines
      }
    });
    rl.on('close', () => exit());
    return () => rl.close();
  }, [handleBackendMessage, exit]);

  const handleSubmit = useCallback((text: string) => {
    sendToBackend({ type: 'user_input', text });
  }, []);

  const handlePermission = useCallback((action: 'allow' | 'deny' | 'always') => {
    sendToBackend({ type: 'permission_response', action });
    setPermissionRequest(null);
  }, []);

  return (
    <Box flexDirection="column">
      {welcomeData && <Banner data={welcomeData} />}
      <ChatLog entries={entries} currentText={currentText} />
      {isThinking && <ThinkingSpinner />}
      {permissionRequest ? (
        <PermissionDialog
          toolName={permissionRequest.toolName}
          args={permissionRequest.args}
          onAction={handlePermission}
        />
      ) : (
        <InputBar onSubmit={handleSubmit} disabled={isThinking} />
      )}
    </Box>
  );
}
