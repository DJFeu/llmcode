import * as vscode from 'vscode';
import {
  OpenFileParams,
  DiagnosticsParams,
  ShowDiffParams,
  DiagnosticItem,
  SelectionResult,
  makeResponse,
  makeError,
  JsonRpcRequest,
} from './protocol';

type SendFn = (data: string) => void;

export async function handleRequest(req: JsonRpcRequest, send: SendFn): Promise<void> {
  try {
    switch (req.method) {
      case 'ide/openFile':
        await handleOpenFile(req.params as unknown as OpenFileParams, req.id, send);
        break;
      case 'ide/diagnostics':
        await handleDiagnostics(req.params as unknown as DiagnosticsParams, req.id, send);
        break;
      case 'ide/selection':
        await handleSelection(req.id, send);
        break;
      case 'ide/showDiff':
        await handleShowDiff(req.params as unknown as ShowDiffParams, req.id, send);
        break;
      default:
        send(makeError(req.id, -32601, `Method not found: ${req.method}`));
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    send(makeError(req.id, -32000, msg));
  }
}

async function handleOpenFile(params: OpenFileParams, id: number, send: SendFn): Promise<void> {
  const uri = vscode.Uri.file(params.path);
  const doc = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(doc, { preview: false });

  if (params.line !== undefined && params.line > 0) {
    const line = Math.max(0, params.line - 1);
    const range = new vscode.Range(line, 0, line, 0);
    editor.selection = new vscode.Selection(range.start, range.start);
    editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
  }

  send(makeResponse(id, { ok: true }));
}

async function handleDiagnostics(params: DiagnosticsParams, id: number, send: SendFn): Promise<void> {
  const uri = vscode.Uri.file(params.path);
  const diags = vscode.languages.getDiagnostics(uri);

  const items: DiagnosticItem[] = diags.map((d) => ({
    line: d.range.start.line + 1,
    severity: vscode.DiagnosticSeverity[d.severity],
    message: d.message,
    source: d.source ?? '',
  }));

  send(makeResponse(id, { diagnostics: items }));
}

async function handleSelection(id: number, send: SendFn): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) {
    send(makeResponse(id, null));
    return;
  }

  const sel = editor.selection;
  const text = editor.document.getText(sel);
  const result: SelectionResult = {
    path: editor.document.uri.fsPath,
    start_line: sel.start.line + 1,
    end_line: sel.end.line + 1,
    text,
  };

  send(makeResponse(id, result));
}

async function handleShowDiff(params: ShowDiffParams, id: number, send: SendFn): Promise<void> {
  const oldUri = vscode.Uri.parse(`llmcode-diff:old/${params.path}`);
  const newUri = vscode.Uri.parse(`llmcode-diff:new/${params.path}`);

  const provider = new (class implements vscode.TextDocumentContentProvider {
    provideTextDocumentContent(uri: vscode.Uri): string {
      return uri.path.startsWith('old/') ? params.old_text : params.new_text;
    }
  })();

  const registration = vscode.workspace.registerTextDocumentContentProvider('llmcode-diff', provider);

  const fileName = params.path.split('/').pop() ?? params.path;
  await vscode.commands.executeCommand('vscode.diff', oldUri, newUri, `llmcode diff: ${fileName}`);

  setTimeout(() => registration.dispose(), 5000);

  send(makeResponse(id, { ok: true }));
}
