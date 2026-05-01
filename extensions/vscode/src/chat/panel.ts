import * as vscode from 'vscode';
import WebSocket from 'ws';
import { LlmcodeProcess } from './process';
import { getConfig } from '../config';
import { FormalServerClient } from './formal-client';

export class ChatPanelProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'llmcode.chatView';

  private view?: vscode.WebviewView;
  private ws: WebSocket | null = null;
  private formalClient: FormalServerClient | null = null;
  private proc: LlmcodeProcess | null = null;
  private extensionUri: vscode.Uri;

  constructor(extensionUri: vscode.Uri) {
    this.extensionUri = extensionUri;
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri],
    };

    webviewView.webview.html = this.getHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg.type === 'send') {
        this.sendToServer(msg.text);
      } else if (msg.type === 'ready') {
        this.connectToServer();
      }
    });

    webviewView.onDidDispose(() => {
      this.disconnectServer();
    });
  }

  sendMessage(text: string): void {
    this.sendToServer(text);
  }

  private async connectToServer(): Promise<void> {
    const config = getConfig();
    this.disconnectServer();

    if (config.chatProtocol === 'formal') {
      this.formalClient = new FormalServerClient(
        {
          url: config.formalServerUrl,
          token: config.formalServerToken,
          sessionId: config.formalSessionId,
          role: config.formalRole,
        },
        (message) => this.postToWebview(message),
      );
      try {
        await this.formalClient.connect();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.formalClient.close();
        this.formalClient = null;
        this.postToWebview({ type: 'error', message: msg });
      }
      return;
    }

    let url = config.serverUrl;

    if (!url) {
      if (!config.autoSpawn) {
        this.postToWebview({ type: 'error', message: 'No server URL configured and autoSpawn is disabled' });
        return;
      }

      this.proc = new LlmcodeProcess();
      try {
        const port = await this.proc.start(config.pythonPath);
        url = `ws://127.0.0.1:${port}`;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.postToWebview({ type: 'error', message: msg });
        return;
      }
    }

    this.ws = new WebSocket(url);

    this.ws.on('open', () => {
      this.postToWebview({ type: 'status', state: 'connected' });
    });

    this.ws.on('message', (data: WebSocket.Data) => {
      try {
        const event = JSON.parse(data.toString());
        this.postToWebview(event);
      } catch {
        // ignore
      }
    });

    this.ws.on('close', () => {
      this.postToWebview({ type: 'status', state: 'disconnected' });
    });

    this.ws.on('error', () => {
      // close will fire
    });
  }

  private sendToServer(text: string): void {
    if (this.formalClient) {
      this.formalClient.send(text).catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        this.postToWebview({ type: 'error', message: msg });
      });
      return;
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'user_input', text }));
    } else {
      this.postToWebview({ type: 'error', message: 'Not connected to llmcode server' });
    }
  }

  private disconnectServer(): void {
    if (this.formalClient) {
      this.formalClient.close();
      this.formalClient = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    if (this.proc) {
      this.proc.stop();
      this.proc = null;
    }
  }

  private postToWebview(msg: Record<string, unknown>): void {
    this.view?.webview.postMessage(msg);
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, 'src', 'chat', 'webview', 'style.css'),
    );

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <link rel="stylesheet" href="${styleUri}" />
</head>
<body>
  <div id="chat-container">
    <div id="messages"></div>
    <div id="input-area">
      <textarea id="input" rows="2" placeholder="Ask llmcode..."></textarea>
      <button id="send-btn">Send</button>
    </div>
  </div>
  <script nonce="${nonce}">
    ${getWebviewScript()}
  </script>
</body>
</html>`;
  }
}

function getNonce(): string {
  let text = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

function getWebviewScript(): string {
  // All dynamic content uses textContent (safe) — no innerHTML with user data
  return `
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    let currentAssistant = null;

    function appendMessage(cls, text) {
      const div = document.createElement('div');
      div.className = 'message ' + cls;
      div.textContent = text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    function appendToolBadge(name, detail) {
      const div = document.createElement('div');
      div.className = 'message assistant';
      const badge = document.createElement('span');
      badge.className = 'tool-badge';
      badge.textContent = name + (detail ? ' ' + detail : '');
      div.appendChild(badge);
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    sendBtn.addEventListener('click', () => {
      const text = inputEl.value.trim();
      if (!text) return;
      appendMessage('user', text);
      vscode.postMessage({ type: 'send', text });
      inputEl.value = '';
      currentAssistant = null;
    });

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendBtn.click();
      }
    });

    window.addEventListener('message', (e) => {
      const msg = e.data;
      switch (msg.type) {
        case 'welcome':
          appendMessage('assistant', 'Connected to ' + (msg.model || 'llmcode'));
          break;
        case 'user_echo':
          break;
        case 'text_delta':
          if (!currentAssistant) {
            currentAssistant = appendMessage('assistant', '');
          }
          currentAssistant.textContent += msg.text;
          messagesEl.scrollTop = messagesEl.scrollHeight;
          break;
        case 'text_done':
          if (!currentAssistant) {
            currentAssistant = appendMessage('assistant', '');
          }
          if (msg.text) {
            currentAssistant.textContent += msg.text;
          }
          currentAssistant = null;
          break;
        case 'thinking_start':
          break;
        case 'thinking_stop':
          break;
        case 'tool_start':
          appendToolBadge(msg.name, msg.detail || '');
          break;
        case 'tool_result':
          if (msg.isError) {
            appendMessage('error', msg.output || 'Tool error');
          }
          break;
        case 'turn_done':
          currentAssistant = null;
          break;
        case 'error':
          appendMessage('error', msg.message || 'Unknown error');
          break;
        case 'status':
          if (msg.state === 'disconnected') {
            appendMessage('error', 'Disconnected from server');
          }
          break;
        case 'message':
          appendMessage('assistant', msg.text || '');
          break;
      }
    });

    vscode.postMessage({ type: 'ready' });
  `;
}
