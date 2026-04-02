import React from 'react';
import { render } from 'ink';
import { App } from './App.js';
import tty from 'tty';
import fs from 'fs';

// Ink renders to stderr (which goes directly to terminal).
// stdout is reserved for JSON-lines IPC back to Python.
// stdin comes from /dev/tty (keyboard) since process.stdin is piped from Python.

let inputStream: NodeJS.ReadStream;
try {
  const fd = fs.openSync('/dev/tty', 'r+');
  inputStream = new tty.ReadStream(fd);
} catch {
  inputStream = process.stdin as NodeJS.ReadStream;
}

// Create a write stream to stderr for Ink rendering
const outputStream = new tty.WriteStream(2); // fd 2 = stderr

render(<App />, {
  stdin: inputStream,
  stdout: outputStream,
});
