import * as vscode from 'vscode';
import { BridgeClient } from './bridge/client';
import { ChatPanelProvider } from './chat/panel';
import { StatusBar } from './ui/status-bar';
import { getConfig } from './config';
import { registerAskCommand } from './actions/ask';
import { registerDiagnosticsFixCommand } from './actions/diagnostics-fix';

let bridge: BridgeClient | null = null;
let statusBar: StatusBar | null = null;

export function activate(context: vscode.ExtensionContext): void {
  const config = getConfig();

  // Status bar
  statusBar = new StatusBar();
  context.subscriptions.push(statusBar);

  // Bridge client
  const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
  bridge = new BridgeClient(config.bridgePort, workspacePath);
  context.subscriptions.push(bridge);

  bridge.onStateChange((state) => {
    statusBar?.update(state);
  });

  if (config.autoConnect) {
    bridge.connect();
  }

  // Chat panel
  const chatProvider = new ChatPanelProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatPanelProvider.viewType, chatProvider),
  );

  // Commands
  context.subscriptions.push(
    vscode.commands.registerCommand('llmcode.connect', () => bridge?.connect()),
    vscode.commands.registerCommand('llmcode.disconnect', () => bridge?.disconnect()),
    vscode.commands.registerCommand('llmcode.openChat', () => {
      vscode.commands.executeCommand('llmcode.chatView.focus');
    }),
  );

  // Code actions
  registerAskCommand(context, chatProvider);
  registerDiagnosticsFixCommand(context, chatProvider);
}

export function deactivate(): void {
  bridge?.dispose();
  bridge = null;
  statusBar = null;
}
