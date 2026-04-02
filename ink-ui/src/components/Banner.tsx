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

const LOGO = [
  '  ██╗     ██╗     ███╗   ███╗',
  '  ██║     ██║     ████╗ ████║',
  '  ██║     ██║     ██╔████╔██║',
  '  ██║     ██║     ██║╚██╔╝██║',
  '  ███████╗███████╗██║ ╚═╝ ██║',
  '  ╚══════╝╚══════╝╚═╝     ╚═╝',
  '   ██████╗ ██████╗ ██████╗ ███████╗',
  '  ██╔════╝██╔═══██╗██╔══██╗██╔════╝',
  '  ██║     ██║   ██║██║  ██║█████╗  ',
  '  ██║     ██║   ██║██║  ██║██╔══╝  ',
  '  ╚██████╗╚██████╔╝██████╔╝███████╗',
  '   ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝',
];

export function Banner({ data }: BannerProps) {
  const workspace = data.branch ? `${data.workspace} · ${data.branch}` : data.workspace;
  const pasteKey = process.platform === 'darwin' ? 'Cmd+V' : 'Ctrl+V';

  const info: [string, string][] = [
    ['Model', data.model || '(not set)'],
    ['Workspace', workspace],
    ['Directory', data.cwd],
    ['Permissions', data.permissions],
    ['', ''],
    ['Quick start', '/help · /skill · /mcp'],
    ['Multiline', 'Shift+Enter'],
    ['Images', `${pasteKey} pastes`],
  ];

  return (
    <Box flexDirection="row" marginBottom={1}>
      <Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1}>
        {LOGO.map((line, i) => (
          <Text key={i} color="cyan" bold>{line}</Text>
        ))}
      </Box>

      <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={2} marginLeft={1}>
        <Text bold color="cyan">Local LLM Agent</Text>
        <Text color="gray">────────────────────</Text>
        {info.map(([label, value], i) => (
          label ? (
            <Text key={i}>
              <Text dimColor>{label.padEnd(14)}</Text>
              <Text>{value}</Text>
            </Text>
          ) : (
            <Text key={i}> </Text>
          )
        ))}
        <Text color="gray">────────────────────</Text>
        <Text color="green">Ready</Text>
      </Box>
    </Box>
  );
}
