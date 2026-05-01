import WebSocket from 'ws';

export type FormalRole = 'writer' | 'observer';

export interface FormalServerClientOptions {
  readonly url: string;
  readonly token: string;
  readonly sessionId?: string;
  readonly role?: FormalRole;
}

export type WebviewMessage = Record<string, unknown> & { type: string };

interface JsonRpcResponse {
  readonly jsonrpc: '2.0';
  readonly id: number;
  readonly result?: unknown;
  readonly error?: { code: number; message: string };
}

interface JsonRpcNotification {
  readonly jsonrpc: '2.0';
  readonly method: string;
  readonly params?: Record<string, unknown>;
}

interface PendingRequest {
  readonly resolve: (value: unknown) => void;
  readonly reject: (reason: Error) => void;
  readonly timer: ReturnType<typeof setTimeout>;
}

const KNOWN_EVENT_TYPES = new Set([
  'text_delta',
  'text_done',
  'tool_start',
  'tool_result',
  'turn_done',
  'message',
  'error',
  'welcome',
  'status',
]);

export function normalizeFormalServerUrl(raw: string): string {
  const value = raw.trim();
  if (!value) {
    return '';
  }
  if (value.startsWith('ws://') || value.startsWith('wss://')) {
    return value;
  }
  if (value.startsWith('http://')) {
    return `ws://${value.slice('http://'.length)}`;
  }
  if (value.startsWith('https://')) {
    return `wss://${value.slice('https://'.length)}`;
  }
  return `ws://${value}`;
}

export function mapFormalPayloadToWebviewMessage(payload: Record<string, unknown>): WebviewMessage {
  const type = typeof payload.type === 'string' ? payload.type : '';
  if (type === 'user_message') {
    return { type: 'user_echo', text: String(payload.text ?? '') };
  }
  if (KNOWN_EVENT_TYPES.has(type)) {
    return { ...payload, type };
  }
  return {
    type: 'message',
    text: JSON.stringify(payload),
  };
}

export class FormalServerClient {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingRequest>();
  private sessionId: string | null;
  private readonly role: FormalRole;
  private reportedDisconnected = false;

  constructor(
    private readonly options: FormalServerClientOptions,
    private readonly onMessage: (message: WebviewMessage) => void,
  ) {
    this.sessionId = options.sessionId?.trim() || null;
    this.role = options.role ?? 'writer';
  }

  async connect(): Promise<void> {
    const url = normalizeFormalServerUrl(this.options.url);
    const token = this.options.token.trim();
    if (!url) {
      throw new Error('llmcode.formalServerUrl is required for formal chat protocol');
    }
    if (!token) {
      throw new Error('llmcode.formalServerToken or LLMCODE_SERVER_TOKEN is required for formal chat protocol');
    }

    this.reportedDisconnected = false;
    this.ws = new WebSocket(url, {
      headers: { Authorization: `Bearer ${token}` },
    });

    this.ws.on('message', (data: WebSocket.Data) => {
      this.handleMessage(data.toString());
    });
    this.ws.on('close', () => {
      this.emitDisconnected();
    });

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Timed out connecting to ${url}`));
      }, 10_000);

      this.ws?.once('open', () => {
        clearTimeout(timer);
        resolve();
      });
      this.ws?.once('error', (err) => {
        clearTimeout(timer);
        reject(err instanceof Error ? err : new Error(String(err)));
      });
    });

    if (!this.sessionId) {
      const created = await this.call('session.create', {});
      this.sessionId = readSessionId(created);
    }

    await this.call('session.attach', {
      session_id: this.sessionId,
      role: this.role,
      last_event_id: 0,
    });
    await this.call('session.subscribe_events', { session_id: this.sessionId });
    this.onMessage({ type: 'status', state: 'connected' });
  }

  async send(text: string): Promise<void> {
    if (!this.sessionId) {
      throw new Error('No formal llmcode session is attached');
    }
    await this.call('session.send', { session_id: this.sessionId, text });
  }

  close(): void {
    for (const [id, request] of this.pending) {
      clearTimeout(request.timer);
      request.reject(new Error('Formal server connection closed'));
      this.pending.delete(id);
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.emitDisconnected();
  }

  private async call(method: string, params: Record<string, unknown>): Promise<unknown> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Formal server is not connected');
    }
    const id = this.nextId++;
    const frame = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    const result = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Formal server did not respond to ${method} within 10s`));
      }, 10_000);
      this.pending.set(id, { resolve, reject, timer });
    });
    this.ws.send(frame);
    return result;
  }

  private handleMessage(raw: string): void {
    let msg: JsonRpcResponse | JsonRpcNotification;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    if ('method' in msg && msg.method === 'session.event') {
      const payload = msg.params?.payload;
      if (isRecord(payload)) {
        this.onMessage(mapFormalPayloadToWebviewMessage(payload));
      }
      return;
    }

    if ('id' in msg) {
      const pending = this.pending.get(msg.id);
      if (!pending) {
        return;
      }
      clearTimeout(pending.timer);
      this.pending.delete(msg.id);
      if (msg.error) {
        pending.reject(new Error(msg.error.message));
      } else {
        pending.resolve(msg.result);
      }
    }
  }

  private emitDisconnected(): void {
    if (this.reportedDisconnected) {
      return;
    }
    this.reportedDisconnected = true;
    this.onMessage({ type: 'status', state: 'disconnected' });
  }
}

function readSessionId(value: unknown): string {
  if (isRecord(value) && typeof value.session_id === 'string' && value.session_id) {
    return value.session_id;
  }
  throw new Error('Formal server session.create did not return a session_id');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
