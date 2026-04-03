import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';

interface ThinkingPanelProps {
  content: string;
  isThinking: boolean;
  elapsed: number;
  tokens: number;
}

export function ThinkingPanel({ content, isThinking, elapsed, tokens }: ThinkingPanelProps) {
  const [expanded, setExpanded] = useState(false);

  // Reset expansion when new thinking starts
  useEffect(() => {
    if (isThinking) {
      setExpanded(false);
    }
  }, [isThinking]);

  if (!content && !isThinking) return null;

  const header = isThinking
    ? `∴ Thinking… ${elapsed.toFixed(1)}s`
    : `∴ Thought ${elapsed.toFixed(1)}s · ${tokens} tok`;

  return (
    <Box flexDirection="column" marginLeft={1}>
      <Text dimColor italic>
        {header}
        {!isThinking && content ? (expanded ? ' ▾' : ' ▸') : ''}
      </Text>
      {expanded && content && (
        <Box marginLeft={2} marginTop={0}>
          <Text dimColor italic wrap="wrap">
            {content.length > 2000 ? content.slice(0, 2000) + '…' : content}
          </Text>
        </Box>
      )}
    </Box>
  );
}
