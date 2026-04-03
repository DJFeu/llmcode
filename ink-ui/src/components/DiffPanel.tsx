import React from 'react';
import { Box, Text } from 'ink';
import type { DiffData } from '../protocol.js';

interface DiffPanelProps {
  filename: string;
  diff: DiffData;
}

export function DiffPanel({ filename, diff }: DiffPanelProps) {
  return (
    <Box flexDirection="column" marginLeft={2}>
      <Text>
        <Text bold dimColor>{filename}</Text>
        {'  '}
        <Text color="green" bold>+{diff.additions}</Text>
        {'  '}
        <Text color="red" bold>-{diff.deletions}</Text>
      </Text>
      {diff.hunks.map((hunk, hi) => (
        <Box key={hi} flexDirection="column">
          <Text dimColor>
            @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@
          </Text>
          {hunk.lines.slice(0, 30).map((line, li) => {
            if (line.startsWith('+')) {
              return (
                <Text key={li}>
                  <Text dimColor>{String(hunk.new_start + li).padStart(4)} </Text>
                  <Text color="green">{line}</Text>
                </Text>
              );
            }
            if (line.startsWith('-')) {
              return (
                <Text key={li}>
                  <Text dimColor>{'    '} </Text>
                  <Text color="red">{line}</Text>
                </Text>
              );
            }
            return (
              <Text key={li}>
                <Text dimColor>{String(hunk.old_start + li).padStart(4)} </Text>
                <Text>{line}</Text>
              </Text>
            );
          })}
        </Box>
      ))}
    </Box>
  );
}
