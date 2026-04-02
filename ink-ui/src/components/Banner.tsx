import React from 'react';
import { Box, Text } from 'ink';

interface BannerProps {
  data: {
    model: string;
    workspace: string;
    cwd: string;
    permissions: string;
    branch: string;
  };
}

export function Banner({ data }: BannerProps) {
  const workspace = data.branch ? `${data.workspace} · ${data.branch}` : data.workspace;
  const pasteKey = process.platform === 'darwin' ? 'Cmd+V' : 'Ctrl+V';

  const lines: [string, string][] = [
    ['Model', data.model],
    ['Workspace', workspace],
    ['Directory', data.cwd],
    ['Permissions', data.permissions],
    ['Quick start', '/help · /skill · /mcp'],
    ['Multiline', 'Shift+Enter inserts a newline'],
    ['Images', `${pasteKey} pastes from clipboard`],
  ];

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="cyan">  ╭──────────────╮</Text>
      <Text bold color="cyan">  │   llm-code   │</Text>
      <Text color="cyan">  ╰──────────────╯</Text>
      {lines.map(([label, value], i) => (
        <Text key={i}>
          <Text dimColor>  {label.padEnd(17)}</Text>
          <Text>{value}</Text>
        </Text>
      ))}
    </Box>
  );
}
