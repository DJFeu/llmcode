const assert = require('node:assert/strict');
const test = require('node:test');

const { PendingMessageQueue } = require('../out/chat/pending-message-queue');

test('pending message queue drains messages in send order', () => {
  const queue = new PendingMessageQueue();

  queue.enqueue('first');
  queue.enqueue('second');

  assert.equal(queue.size, 2);
  assert.deepEqual(queue.drain(), ['first', 'second']);
  assert.equal(queue.size, 0);
});

test('pending message queue ignores blank messages', () => {
  const queue = new PendingMessageQueue();

  queue.enqueue('  ');
  queue.enqueue('\n');

  assert.deepEqual(queue.drain(), []);
});
