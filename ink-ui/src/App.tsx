import React, { useState, useEffect, useCallback } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import { Banner } from './components/Banner.js';
import { ChatLog, ChatEntry } from './components/ChatLog.js';
import { InputBar } from './components/InputBar.js';
import { ThinkingSpinner } from './components/ThinkingSpinner.js';
import { PermissionDialog } from './components/PermissionDialog.js';
import { MarketplaceSelect } from './components/MarketplaceSelect.js';
import { ActionSelect } from './components/ActionSelect.js';
import { StatusBar } from './components/StatusBar.js';
import type { BackendMessage, FrontendMessage, MarketplaceItem } from './protocol.js';
import * as readline from 'readline';

function sendToBackend(msg: FrontendMessage) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

export function App() {
  const { exit } = useApp();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [currentText, setCurrentText] = useState('');
  const [totalTokens, setTotalTokens] = useState(0);
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
  const [marketplace, setMarketplace] = useState<{title: string; items: MarketplaceItem[]} | null>(null);
  const [actionPicker, setActionPicker] = useState<{name: string; actions: Array<{id: string; label: string}>} | null>(null);

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
        setTotalTokens(prev => prev + (msg.tokens || 0));
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
      case 'marketplace_show':
        setMarketplace({ title: msg.title, items: msg.items });
        break;
      case 'action_show':
        setActionPicker({ name: msg.name, actions: msg.actions });
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

  useInput((_input, key) => {
    if (key.escape && isThinking) {
      sendToBackend({ type: 'user_input', text: '/cancel' });
      setIsThinking(false);
    }
  });

  const handlePermission = useCallback((action: 'allow' | 'deny' | 'always') => {
    sendToBackend({ type: 'permission_response', action });
    setPermissionRequest(null);
  }, []);

  return (
    <Box flexDirection="column">
      {welcomeData && <Banner data={welcomeData} />}
      <ChatLog entries={entries} currentText={currentText} />
      {isThinking && <ThinkingSpinner />}
      {marketplace && (
        <MarketplaceSelect
          title={marketplace.title}
          items={marketplace.items}
          onSelect={(item) => {
            sendToBackend({ type: 'marketplace_select', index: item.index });
            setMarketplace(null);
          }}
          onCancel={() => {
            sendToBackend({ type: 'marketplace_close' });
            setMarketplace(null);
          }}
        />
      )}
      {actionPicker && (
        <ActionSelect
          title={actionPicker.name}
          actions={actionPicker.actions}
          onSelect={(actionId) => {
            sendToBackend({ type: 'action_select', actionId });
            setActionPicker(null);
          }}
          onCancel={() => {
            setActionPicker(null);
          }}
        />
      )}
      {permissionRequest ? (
        <PermissionDialog
          toolName={permissionRequest.toolName}
          args={permissionRequest.args}
          onAction={handlePermission}
        />
      ) : (
        <InputBar onSubmit={handleSubmit} disabled={isThinking} />
      )}
      <StatusBar
        model={welcomeData?.model || ''}
        tokens={totalTokens}
        isThinking={isThinking}
      />
    </Box>
  );
}
