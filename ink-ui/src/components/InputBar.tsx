import React, { useState, useMemo } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';

const SLASH_COMMANDS = [
  { cmd: '/help', desc: 'Show commands' },
  { cmd: '/clear', desc: 'Clear conversation' },
  { cmd: '/model', desc: 'Switch model' },
  { cmd: '/skill', desc: 'Browse skills' },
  { cmd: '/skill search', desc: 'Search skills' },
  { cmd: '/skill install', desc: 'Install skill' },
  { cmd: '/mcp', desc: 'Browse MCP servers' },
  { cmd: '/mcp install', desc: 'Install MCP server' },
  { cmd: '/plugin', desc: 'Browse plugins' },
  { cmd: '/plugin install', desc: 'Install plugin' },
  { cmd: '/memory', desc: 'Project memory' },
  { cmd: '/memory set', desc: 'Store memory' },
  { cmd: '/memory get', desc: 'Recall memory' },
  { cmd: '/session list', desc: 'List sessions' },
  { cmd: '/session save', desc: 'Save session' },
  { cmd: '/undo', desc: 'Undo last change' },
  { cmd: '/index', desc: 'Project index' },
  { cmd: '/image', desc: 'Attach image' },
  { cmd: '/cost', desc: 'Token usage' },
  { cmd: '/budget', desc: 'Set token budget' },
  { cmd: '/cd', desc: 'Change directory' },
  { cmd: '/exit', desc: 'Quit' },
];

interface InputBarProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function InputBar({ onSubmit, disabled }: InputBarProps) {
  const [value, setValue] = useState('');
  const [selectedHint, setSelectedHint] = useState(0);

  const suggestions = useMemo(() => {
    if (!value.startsWith('/') || value.includes(' ') && !value.startsWith('/skill ') && !value.startsWith('/mcp ') && !value.startsWith('/plugin ') && !value.startsWith('/memory ') && !value.startsWith('/session ')) {
      return [];
    }
    return SLASH_COMMANDS.filter(c => c.cmd.startsWith(value.toLowerCase())).slice(0, 8);
  }, [value]);

  useInput((input, key) => {
    if (suggestions.length > 0) {
      if (key.downArrow) {
        setSelectedHint(s => Math.min(s + 1, suggestions.length - 1));
      } else if (key.upArrow) {
        setSelectedHint(s => Math.max(s - 1, 0));
      } else if (key.tab) {
        // Tab: fill the suggestion into input
        setValue(suggestions[selectedHint].cmd + ' ');
        setSelectedHint(0);
      } else if (key.return && selectedHint >= 0) {
        // Enter on suggestion: submit the selected command directly
        const cmd = suggestions[selectedHint].cmd.trim();
        onSubmit(cmd);
        setValue('');
        setSelectedHint(0);
      }
    }
  });

  const handleChange = (newValue: string) => {
    setValue(newValue);
    setSelectedHint(0);
  };

  const handleSubmit = (text: string) => {
    if (suggestions.length > 0) {
      // If suggestions visible, submit the highlighted one
      const cmd = suggestions[selectedHint].cmd.trim();
      onSubmit(cmd);
      setValue('');
      setSelectedHint(0);
    } else if (text.trim()) {
      onSubmit(text.trim());
      setValue('');
      setSelectedHint(0);
    }
  };

  return (
    <Box flexDirection="column">
      {suggestions.length > 0 && (
        <Box flexDirection="column" marginBottom={0}>
          {suggestions.map((s, i) => (
            <Text key={s.cmd}>
              <Text color={i === selectedHint ? 'cyan' : undefined} bold={i === selectedHint}>
                {i === selectedHint ? '❯ ' : '  '}
              </Text>
              <Text color={i === selectedHint ? 'cyan' : 'white'} bold={i === selectedHint}>
                {s.cmd}
              </Text>
              <Text dimColor>  {s.desc}</Text>
            </Text>
          ))}
        </Box>
      )}
      <Box>
        <Text color="cyan" bold>❯ </Text>
        <TextInput
          value={value}
          onChange={handleChange}
          onSubmit={handleSubmit}
          placeholder={disabled ? 'Thinking...' : 'Type a message... (/help for commands)'}
        />
      </Box>
    </Box>
  );
}
