import React from 'react';
import { Text } from 'ink';
import type { VimMode } from '../vim/types.js';

interface VimStatusProps {
  mode: VimMode;
}

export function VimStatus({ mode }: VimStatusProps) {
  const display = mode === 'normal' ? '-- NORMAL --' : '-- INSERT --';
  const color = mode === 'normal' ? 'yellow' : 'green';

  return <Text color={color} bold>{display}</Text>;
}
