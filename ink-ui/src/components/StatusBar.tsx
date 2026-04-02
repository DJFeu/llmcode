import React from 'react';
import { Box, Text } from 'ink';

interface StatusBarProps {
  model: string;
  tokens: number;
  isThinking: boolean;
}

export function StatusBar({ model, tokens, isThinking }: StatusBarProps) {
  return (
    <Box>
      <Text dimColor>
        {model ? `${model}` : ''}
        {tokens > 0 ? ` │ ↓${tokens} tok` : ''}
        {isThinking ? ' │ streaming…' : ''}
        {' │ /help │ Ctrl+D quit'}
      </Text>
    </Box>
  );
}
