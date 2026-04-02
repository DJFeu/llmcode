import React from 'react';
import { Box, Text } from 'ink';

export interface ChatEntry {
  type: 'user' | 'assistant' | 'tool_start' | 'tool_result' | 'status' | 'info' | 'error' | 'help';
  text?: string;
  name?: string;
  detail?: string;
  output?: string;
  isError?: boolean;
  style?: string;
  commands?: Array<{cmd: string; desc: string}>;
}

interface ChatLogProps {
  entries: ChatEntry[];
  currentText: string;
}

export function ChatLog({ entries, currentText }: ChatLogProps) {
  return (
    <Box flexDirection="column">
      {entries.map((entry, i) => (
        <ChatEntryView key={i} entry={entry} />
      ))}
      {currentText && <Text>{currentText}</Text>}
    </Box>
  );
}

function ChatEntryView({ entry }: { entry: ChatEntry }) {
  switch (entry.type) {
    case 'user':
      return <Text><Text bold>❯</Text> {entry.text}</Text>;

    case 'assistant':
      return (
        <Box marginY={0}>
          <Text>{entry.text}</Text>
        </Box>
      );

    case 'tool_start':
      return (
        <Box flexDirection="column" marginY={0}>
          <Text>
            <Text dimColor>  ╭─ </Text>
            <Text bold color="cyan">{entry.name}</Text>
            <Text dimColor> ─╮</Text>
          </Text>
          <Text>
            <Text dimColor>  │ </Text>
            <Text>{entry.detail}</Text>
          </Text>
          <Text dimColor>  ╰{'─'.repeat((entry.name?.length || 0) + 4)}╯</Text>
        </Box>
      );

    case 'tool_result': {
      if (entry.isError) {
        return <Text color="red">  ✗ {entry.output?.slice(0, 150)}</Text>;
      }
      const lines = entry.output?.split('\n').slice(0, 5) || [];
      return (
        <Box flexDirection="column">
          {lines.map((line, i) => {
            if (line.startsWith('- ')) return <Text key={i} color="red">  {line}</Text>;
            if (line.startsWith('+ ')) return <Text key={i} color="green">  {line}</Text>;
            return <Text key={i}><Text color="green">  ✓</Text> <Text dimColor>{line.slice(0, 150)}</Text></Text>;
          })}
        </Box>
      );
    }

    case 'status':
      return <Text color="green">{entry.text}</Text>;

    case 'info':
      return <Text dimColor>{entry.text}</Text>;

    case 'error':
      return <Text color="red" bold>{entry.text}</Text>;

    case 'help':
      return (
        <Box flexDirection="column">
          {entry.commands?.map((c, i) => (
            <Text key={i}>
              <Text dimColor>  {c.cmd.padEnd(30)}</Text>
              <Text dimColor>{c.desc}</Text>
            </Text>
          ))}
        </Box>
      );

    default:
      return null;
  }
}
