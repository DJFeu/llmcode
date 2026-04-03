import React from 'react';
import { Box, Text } from 'ink';
import { VimStatus } from './VimStatus.js';
import type { VimMode } from '../vim/types.js';

interface StatusBarProps {
  model: string;
  tokens: number;
  isThinking: boolean;
  vimEnabled?: boolean;
  vimMode?: VimMode;
}

export function StatusBar({ model, tokens, isThinking, vimEnabled, vimMode }: StatusBarProps) {
  return (
    <Box>
      {vimEnabled && vimMode && (
        <>
          <VimStatus mode={vimMode} />
          <Text dimColor> │ </Text>
        </>
      )}
      <Text dimColor>
        {model ? `${model}` : ''}
        {tokens > 0 ? ` │ ↓${tokens} tok` : ''}
        {isThinking ? ' │ streaming…' : ''}
        {' │ /help │ Ctrl+D quit'}
      </Text>
    </Box>
  );
}
