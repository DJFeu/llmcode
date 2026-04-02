import React from 'react';
import { render } from 'ink';
import { App } from './App.js';
import { PassThrough } from 'stream';
import tty from 'tty';
import fs from 'fs';

// Ink requires a TTY stdin with setRawMode for keyboard interaction.
// When the process stdin is a pipe (JSON-lines from Python backend), we open
// /dev/tty separately so the user can still type. If /dev/tty is unavailable
// (CI, no controlling terminal) we fall back to a PassThrough that silently
// ignores raw-mode requests so Ink won't crash.
function resolveStdin(): NodeJS.ReadStream {
  try {
    const fd = fs.openSync('/dev/tty', 'r+');
    return new tty.ReadStream(fd);
  } catch {
    // No controlling terminal — provide a non-throwing stub stdin.
    // Set isTTY=true so Ink believes raw mode is supported and won't throw,
    // then stub out setRawMode as a no-op.
    const stub = new PassThrough() as unknown as NodeJS.ReadStream;
    stub.isTTY = true;
    (stub as unknown as { setRawMode: (v: boolean) => void }).setRawMode = () => {};
    // ref/unref stubs required by Ink
    (stub as unknown as { ref: () => void }).ref = () => {};
    (stub as unknown as { unref: () => void }).unref = () => {};
    return stub;
  }
}

const inputStream = resolveStdin();
render(<App />, { stdin: inputStream });
