import React from 'react';
import { render } from 'ink';
import { App } from './App.js';
import tty from 'tty';
import fs from 'fs';

// Architecture:
// - process.stdin: PIPE from Python (JSON-lines IPC messages)
// - process.stdout: PIPE to Python (JSON-lines IPC responses)
// - Ink rendering: stderr (fd 2) which goes to terminal
// - Keyboard input: /dev/tty (separate from piped stdin)

// Keyboard input from /dev/tty
let inputStream: NodeJS.ReadStream;
try {
  const readFd = fs.openSync('/dev/tty', 'r');
  inputStream = new tty.ReadStream(readFd);
} catch {
  // Fallback: create a stub so Ink doesn't crash
  const { PassThrough } = require('stream');
  const stub = new PassThrough();
  stub.isTTY = true;
  stub.setRawMode = () => stub;
  stub.ref = () => {};
  stub.unref = () => {};
  inputStream = stub as unknown as NodeJS.ReadStream;
}

// Ink renders to stderr — goes directly to terminal
// Force color support via environment variable
process.env.FORCE_COLOR = '3'; // 256 color support

const outputStream = process.stderr as unknown as NodeJS.WriteStream;

render(<App />, {
  stdin: inputStream,
  stdout: outputStream,
});
