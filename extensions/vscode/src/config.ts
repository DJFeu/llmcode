import * as vscode from 'vscode';
import type { FormalRole } from './chat/formal-client';

export type ChatProtocol = 'debug' | 'formal';

export interface LlmcodeConfig {
  readonly bridgePort: number;
  readonly autoConnect: boolean;
  readonly autoSpawn: boolean;
  readonly serverUrl: string;
  readonly pythonPath: string;
  readonly chatProtocol: ChatProtocol;
  readonly formalServerUrl: string;
  readonly formalServerToken: string;
  readonly formalSessionId: string;
  readonly formalRole: FormalRole;
}

export function getConfig(): LlmcodeConfig {
  const cfg = vscode.workspace.getConfiguration('llmcode');
  const formalRole = cfg.get<string>('formalRole', 'writer');
  return {
    bridgePort: cfg.get<number>('bridgePort', 9876),
    autoConnect: cfg.get<boolean>('autoConnect', true),
    autoSpawn: cfg.get<boolean>('autoSpawn', true),
    serverUrl: cfg.get<string>('serverUrl', ''),
    pythonPath: cfg.get<string>('pythonPath', ''),
    chatProtocol: cfg.get<ChatProtocol>('chatProtocol', 'debug'),
    formalServerUrl: cfg.get<string>('formalServerUrl', 'ws://127.0.0.1:8080'),
    formalServerToken: cfg.get<string>('formalServerToken', '') || process.env.LLMCODE_SERVER_TOKEN || '',
    formalSessionId: cfg.get<string>('formalSessionId', ''),
    formalRole: formalRole === 'observer' ? 'observer' : 'writer',
  };
}
