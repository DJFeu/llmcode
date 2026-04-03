export type VimMode = 'normal' | 'insert';

export interface Register {
  content: string;
}

export interface VimState {
  readonly buffer: string;
  readonly cursor: number;
  readonly mode: VimMode;
  readonly register: Register;
  readonly pendingKeys: string;
  readonly undoStack: ReadonlyArray<{ buffer: string; cursor: number }>;
}

export function initialState(buffer: string): VimState {
  return {
    buffer,
    cursor: buffer.length,
    mode: 'insert',
    register: { content: '' },
    pendingKeys: '',
    undoStack: [],
  };
}
