import * as vscode from 'vscode';
import { ChatPanelProvider } from '../chat/panel';

export class DiagnosticsFixProvider implements vscode.CodeActionProvider {
  static readonly providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];

  constructor(private readonly chatProvider: ChatPanelProvider) {}

  provideCodeActions(
    document: vscode.TextDocument,
    range: vscode.Range,
  ): vscode.CodeAction[] {
    const diagnostics = vscode.languages.getDiagnostics(document.uri);
    const relevant = diagnostics.filter(
      (d) => d.range.intersection(range) !== undefined,
    );

    if (relevant.length === 0) return [];

    const action = new vscode.CodeAction('Fix with LLMCode', vscode.CodeActionKind.QuickFix);
    action.command = {
      command: 'llmcode.fixDiagnostic',
      title: 'Fix with LLMCode',
      arguments: [document.uri, relevant],
    };
    return [action];
  }
}

export function registerDiagnosticsFixCommand(
  context: vscode.ExtensionContext,
  chatProvider: ChatPanelProvider,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'llmcode.fixDiagnostic',
      (uri: vscode.Uri, diagnostics: vscode.Diagnostic[]) => {
        const relPath = vscode.workspace.asRelativePath(uri);
        const lines = diagnostics.map(
          (d) => `L${d.range.start.line + 1} ${vscode.DiagnosticSeverity[d.severity]}: ${d.message}`,
        );
        const prompt = `Fix these errors in \`${relPath}\`:\n${lines.join('\n')}`;

        vscode.commands.executeCommand('llmcode.chatView.focus');
        chatProvider.sendMessage(prompt);
      },
    ),
  );

  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { scheme: 'file' },
      new DiagnosticsFixProvider(chatProvider),
      { providedCodeActionKinds: DiagnosticsFixProvider.providedCodeActionKinds },
    ),
  );
}
