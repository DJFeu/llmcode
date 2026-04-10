import * as vscode from 'vscode';

export interface LlmcodeConfig {
  readonly bridgePort: number;
  readonly autoConnect: boolean;
  readonly autoSpawn: boolean;
  readonly serverUrl: string;
  readonly pythonPath: string;
}

export function getConfig(): LlmcodeConfig {
  const cfg = vscode.workspace.getConfiguration('llmcode');
  return {
    bridgePort: cfg.get<number>('bridgePort', 9876),
    autoConnect: cfg.get<boolean>('autoConnect', true),
    autoSpawn: cfg.get<boolean>('autoSpawn', true),
    serverUrl: cfg.get<string>('serverUrl', ''),
    pythonPath: cfg.get<string>('pythonPath', ''),
  };
}
