const assert = require('node:assert/strict');
const test = require('node:test');

const {
  FormalServerClient,
  mapFormalPayloadToWebviewMessage,
  normalizeFormalServerUrl,
} = require('../out/chat/formal-client');

test('maps user_message to user_echo to avoid duplicate chat bubbles', () => {
  assert.deepEqual(
    mapFormalPayloadToWebviewMessage({ type: 'user_message', text: 'hello' }),
    { type: 'user_echo', text: 'hello' },
  );
});

test('passes known assistant stream events through unchanged', () => {
  assert.deepEqual(
    mapFormalPayloadToWebviewMessage({ type: 'text_delta', text: 'hi' }),
    { type: 'text_delta', text: 'hi' },
  );
  assert.deepEqual(
    mapFormalPayloadToWebviewMessage({ type: 'tool_start', name: 'read_file', detail: 'README.md' }),
    { type: 'tool_start', name: 'read_file', detail: 'README.md' },
  );
});

test('normalizes formal server URLs without changing explicit websocket URLs', () => {
  assert.equal(normalizeFormalServerUrl('127.0.0.1:8080'), 'ws://127.0.0.1:8080');
  assert.equal(normalizeFormalServerUrl('http://127.0.0.1:8080'), 'ws://127.0.0.1:8080');
  assert.equal(normalizeFormalServerUrl('https://example.test/server'), 'wss://example.test/server');
  assert.equal(normalizeFormalServerUrl('ws://127.0.0.1:8080'), 'ws://127.0.0.1:8080');
});

test('emits disconnected status when the formal client closes', () => {
  const messages = [];
  const client = new FormalServerClient(
    { url: 'ws://127.0.0.1:8080', token: 'token' },
    (message) => messages.push(message),
  );
  client.close();
  assert.deepEqual(messages, [{ type: 'status', state: 'disconnected' }]);
});
