export interface JsonRpcRequest {
  readonly jsonrpc: '2.0';
  readonly method: string;
  readonly params: Record<string, unknown>;
  readonly id: number;
}

export interface JsonRpcResponse {
  readonly jsonrpc: '2.0';
  readonly id: number;
  readonly result?: unknown;
  readonly error?: { code: number; message: string };
}

export interface OpenFileParams {
  readonly path: string;
  readonly line?: number;
}

export interface DiagnosticsParams {
  readonly path: string;
}

export type SelectionParams = Record<string, never>;

export interface ShowDiffParams {
  readonly path: string;
  readonly old_text: string;
  readonly new_text: string;
}

export interface DiagnosticItem {
  readonly line: number;
  readonly severity: string;
  readonly message: string;
  readonly source: string;
}

export interface SelectionResult {
  readonly path: string;
  readonly start_line: number;
  readonly end_line: number;
  readonly text: string;
}

export function makeResponse(id: number, result: unknown): string {
  return JSON.stringify({ jsonrpc: '2.0', result, id });
}

export function makeError(id: number, code: number, message: string): string {
  return JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id });
}

export function makeRequest(id: number, method: string, params: Record<string, unknown>): string {
  return JSON.stringify({ jsonrpc: '2.0', method, params, id });
}
