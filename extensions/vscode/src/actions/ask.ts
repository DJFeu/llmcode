import * as vscode from 'vscode';
import { ChatPanelProvider } from '../chat/panel';

export function registerAskCommand(
  context: vscode.ExtensionContext,
  chatProvider: ChatPanelProvider,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('llmcode.askAboutSelection', () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.selection.isEmpty) {
        vscode.window.showWarningMessage('No text selected');
        return;
      }

      const sel = editor.selection;
      const text = editor.document.getText(sel);
      const relPath = vscode.workspace.asRelativePath(editor.document.uri);
      const startLine = sel.start.line + 1;
      const endLine = sel.end.line + 1;

      const prompt = `Regarding \`${relPath}\` lines ${startLine}-${endLine}:\n\`\`\`\n${text}\n\`\`\`\n`;

      vscode.commands.executeCommand('llmcode.chatView.focus');
      chatProvider.sendMessage(prompt);
    }),
  );
}
