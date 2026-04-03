/**
 * Simplified vim engine for the Ink frontend.
 * Supports: h/l/w/b/0/$, d/c/y + motions, dd/cc/yy, x, i/a/A/I, Esc, p/P, u.
 * Does NOT implement text objects or char-search — those use prompt_toolkit
 * in lite mode or the Python engine in API mode.
 */
import { VimState, VimMode, initialState } from './types.js';

const WORD_RE = /[A-Za-z0-9_]+|[^\sA-Za-z0-9_]+/g;

function moveW(buffer: string, pos: number): number {
  const matches = [...buffer.matchAll(new RegExp(WORD_RE.source, 'g'))];
  for (const m of matches) {
    if (m.index !== undefined && m.index > pos) {
      return m.index;
    }
  }
  return pos;
}

function moveB(buffer: string, pos: number): number {
  const matches = [...buffer.matchAll(new RegExp(WORD_RE.source, 'g'))];
  for (let i = matches.length - 1; i >= 0; i--) {
    const m = matches[i];
    if (m.index !== undefined && m.index < pos) {
      return m.index;
    }
  }
  return 0;
}

export class VimEngine {
  private state: VimState;

  constructor(buffer = '') {
    this.state = initialState(buffer);
  }

  get buffer(): string { return this.state.buffer; }
  get cursor(): number { return this.state.cursor; }
  get mode(): VimMode { return this.state.mode; }
  get modeDisplay(): string {
    return this.state.mode === 'normal' ? '-- NORMAL --' : '-- INSERT --';
  }

  feedKey(key: string): void {
    if (this.state.mode === 'insert') {
      this.handleInsert(key);
    } else {
      this.handleNormal(key);
    }
  }

  setBuffer(buffer: string): void {
    this.state = { ...this.state, buffer, cursor: buffer.length };
  }

  snapshot(): Readonly<VimState> { return this.state; }

  private handleInsert(key: string): void {
    if (key === '\x1b' || key === 'escape') {
      this.state = {
        ...this.state,
        mode: 'normal',
        cursor: Math.max(0, this.state.cursor - 1),
      };
      return;
    }
    if (key === '\x7f' || key === 'backspace') {
      if (this.state.cursor === 0) return;
      const buf = this.state.buffer;
      const c = this.state.cursor;
      this.state = {
        ...this.state,
        buffer: buf.slice(0, c - 1) + buf.slice(c),
        cursor: c - 1,
      };
      return;
    }
    if (key.length === 1) {
      const buf = this.state.buffer;
      const c = this.state.cursor;
      this.state = {
        ...this.state,
        buffer: buf.slice(0, c) + key + buf.slice(c),
        cursor: c + 1,
      };
    }
  }

  private handleNormal(key: string): void {
    const pending = this.state.pendingKeys + key;

    // Mode switches
    if (pending === 'i') {
      this.state = { ...this.state, mode: 'insert', pendingKeys: '' };
      return;
    }
    if (pending === 'a') {
      this.state = {
        ...this.state,
        mode: 'insert',
        cursor: Math.min(this.state.cursor + 1, this.state.buffer.length),
        pendingKeys: '',
      };
      return;
    }
    if (pending === 'A') {
      this.state = {
        ...this.state,
        mode: 'insert',
        cursor: this.state.buffer.length,
        pendingKeys: '',
      };
      return;
    }
    if (pending === 'I') {
      const firstNonBlank = this.state.buffer.search(/\S/);
      this.state = {
        ...this.state,
        mode: 'insert',
        cursor: firstNonBlank === -1 ? 0 : firstNonBlank,
        pendingKeys: '',
      };
      return;
    }

    // Motions
    if (pending === 'h') {
      this.state = {
        ...this.state,
        cursor: Math.max(0, this.state.cursor - 1),
        pendingKeys: '',
      };
      return;
    }
    if (pending === 'l') {
      this.state = {
        ...this.state,
        cursor: Math.min(Math.max(0, this.state.buffer.length - 1), this.state.cursor + 1),
        pendingKeys: '',
      };
      return;
    }
    if (pending === '0') {
      this.state = { ...this.state, cursor: 0, pendingKeys: '' };
      return;
    }
    if (pending === '$') {
      this.state = {
        ...this.state,
        cursor: Math.max(0, this.state.buffer.length - 1),
        pendingKeys: '',
      };
      return;
    }
    if (pending === 'w') {
      this.state = {
        ...this.state,
        cursor: moveW(this.state.buffer, this.state.cursor),
        pendingKeys: '',
      };
      return;
    }
    if (pending === 'b') {
      this.state = {
        ...this.state,
        cursor: moveB(this.state.buffer, this.state.cursor),
        pendingKeys: '',
      };
      return;
    }

    // x — delete char
    if (pending === 'x') {
      const buf = this.state.buffer;
      const c = this.state.cursor;
      if (buf.length === 0) { this.state = { ...this.state, pendingKeys: '' }; return; }
      const deleted = buf[c] || '';
      const newBuf = buf.slice(0, c) + buf.slice(c + 1);
      this.state = {
        ...this.state,
        buffer: newBuf,
        cursor: Math.min(c, Math.max(0, newBuf.length - 1)),
        register: { content: deleted },
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: buf, cursor: c }],
      };
      return;
    }

    // p — put after
    if (pending === 'p') {
      const content = this.state.register.content;
      if (!content) { this.state = { ...this.state, pendingKeys: '' }; return; }
      const buf = this.state.buffer;
      const c = this.state.cursor + 1;
      this.state = {
        ...this.state,
        buffer: buf.slice(0, c) + content + buf.slice(c),
        cursor: c + content.length - 1,
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: buf, cursor: this.state.cursor }],
      };
      return;
    }

    // P — put before
    if (pending === 'P') {
      const content = this.state.register.content;
      if (!content) { this.state = { ...this.state, pendingKeys: '' }; return; }
      const buf = this.state.buffer;
      const c = this.state.cursor;
      this.state = {
        ...this.state,
        buffer: buf.slice(0, c) + content + buf.slice(c),
        cursor: c + content.length - 1,
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: buf, cursor: c }],
      };
      return;
    }

    // dd — delete line
    if (pending === 'dd') {
      this.state = {
        ...this.state,
        buffer: '',
        cursor: 0,
        register: { content: this.state.buffer },
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: this.state.buffer, cursor: this.state.cursor }],
      };
      return;
    }

    // cc — change line
    if (pending === 'cc') {
      this.state = {
        ...this.state,
        buffer: '',
        cursor: 0,
        mode: 'insert',
        register: { content: this.state.buffer },
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: this.state.buffer, cursor: this.state.cursor }],
      };
      return;
    }

    // yy — yank line
    if (pending === 'yy') {
      this.state = {
        ...this.state,
        register: { content: this.state.buffer },
        pendingKeys: '',
      };
      return;
    }

    // dw — delete word
    if (pending === 'dw') {
      const buf = this.state.buffer;
      const c = this.state.cursor;
      const end = moveW(buf, c);
      const deleted = buf.slice(c, end);
      const newBuf = buf.slice(0, c) + buf.slice(end);
      this.state = {
        ...this.state,
        buffer: newBuf,
        cursor: Math.min(c, Math.max(0, newBuf.length - 1)),
        register: { content: deleted },
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: buf, cursor: c }],
      };
      return;
    }

    // cw — change word (acts like ce: stop before trailing space)
    if (pending === 'cw') {
      const buf = this.state.buffer;
      const c = this.state.cursor;
      // find end of current word (non-whitespace run)
      const wordEnd = buf.slice(c).search(/\s|$/);
      const end = wordEnd === -1 ? buf.length : c + wordEnd;
      const deleted = buf.slice(c, end);
      const newBuf = buf.slice(0, c) + buf.slice(end);
      this.state = {
        ...this.state,
        buffer: newBuf,
        cursor: c,
        mode: 'insert',
        register: { content: deleted },
        pendingKeys: '',
        undoStack: [...this.state.undoStack, { buffer: buf, cursor: c }],
      };
      return;
    }

    // u — undo
    if (pending === 'u') {
      const stack = this.state.undoStack;
      if (stack.length === 0) { this.state = { ...this.state, pendingKeys: '' }; return; }
      const prev = stack[stack.length - 1];
      this.state = {
        ...this.state,
        buffer: prev.buffer,
        cursor: prev.cursor,
        undoStack: stack.slice(0, -1),
        pendingKeys: '',
      };
      return;
    }

    // Pending operator — wait for more input
    if (pending === 'd' || pending === 'c' || pending === 'y') {
      this.state = { ...this.state, pendingKeys: pending };
      return;
    }

    // Unknown — reset
    this.state = { ...this.state, pendingKeys: '' };
  }
}
