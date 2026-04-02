import React from 'react';
import { Text } from 'ink';
import Spinner from 'ink-spinner';

export function ThinkingSpinner() {
  return (
    <Text>
      <Text color="blue"><Spinner type="dots" /></Text>
      <Text color="blue"> Thinking…</Text>
    </Text>
  );
}
