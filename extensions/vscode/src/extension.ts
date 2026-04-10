import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext): void {
  vscode.window.showInformationMessage('LLMCode extension activated');
}

export function deactivate(): void {
  // cleanup
}
