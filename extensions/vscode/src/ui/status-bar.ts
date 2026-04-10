import * as vscode from 'vscode';
import { ConnectionState } from '../bridge/client';

export class StatusBar {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.item.command = 'llmcode.openChat';
    this.update('disconnected');
    this.item.show();
  }

  update(state: ConnectionState): void {
    switch (state) {
      case 'connected':
        this.item.text = '$(plug) llmcode';
        this.item.tooltip = 'LLMCode: Connected';
        this.item.color = undefined;
        break;
      case 'connecting':
        this.item.text = '$(sync~spin) llmcode';
        this.item.tooltip = 'LLMCode: Connecting...';
        this.item.color = new vscode.ThemeColor('statusBarItem.warningForeground');
        break;
      case 'disconnected':
        this.item.text = '$(plug) llmcode';
        this.item.tooltip = 'LLMCode: Disconnected';
        this.item.color = new vscode.ThemeColor('disabledForeground');
        break;
    }
  }

  dispose(): void {
    this.item.dispose();
  }
}
