import WebSocket from 'ws';
import * as vscode from 'vscode';
import { JsonRpcRequest, makeRequest } from './protocol';
import { handleRequest } from './handlers';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected';

export class BridgeClient {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private nextId = 1;
  private disposed = false;

  private readonly _onStateChange = new vscode.EventEmitter<ConnectionState>();
  readonly onStateChange = this._onStateChange.event;

  private _state: ConnectionState = 'disconnected';
  get state(): ConnectionState {
    return this._state;
  }

  constructor(
    private readonly port: number,
    private readonly workspacePath: string,
  ) {}

  connect(): void {
    if (this.disposed) return;
    this.setState('connecting');

    const url = `ws://127.0.0.1:${this.port}`;
    this.ws = new WebSocket(url);

    this.ws.on('open', () => {
      this.reconnectDelay = 1000;
      this.register();
      this.startPing();
      this.setState('connected');
    });

    this.ws.on('message', (data: WebSocket.Data) => {
      this.handleMessage(data.toString());
    });

    this.ws.on('close', () => {
      this.cleanup();
      this.setState('disconnected');
      this.scheduleReconnect();
    });

    this.ws.on('error', () => {
      // close event will fire after error
    });
  }

  disconnect(): void {
    this.cancelReconnect();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.cleanup();
    this.setState('disconnected');
  }

  dispose(): void {
    this.disposed = true;
    this.disconnect();
    this._onStateChange.dispose();
  }

  private register(): void {
    if (!this.ws) return;
    const id = this.nextId++;
    const msg = makeRequest(id, 'ide/register', {
      name: 'vscode',
      pid: process.pid,
      workspace_path: this.workspacePath,
    });
    this.ws.send(msg);
  }

  private handleMessage(raw: string): void {
    let msg: JsonRpcRequest;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    if (msg.method) {
      const send = (data: string): void => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(data);
        }
      };
      handleRequest(msg, send);
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, 30_000);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    this.cancelReconnect();
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30_000);
  }

  private cancelReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private cleanup(): void {
    this.stopPing();
  }

  private setState(state: ConnectionState): void {
    if (this._state !== state) {
      this._state = state;
      this._onStateChange.fire(state);
    }
  }
}
