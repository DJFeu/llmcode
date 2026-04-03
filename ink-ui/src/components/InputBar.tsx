import React, { useState, useMemo, useRef } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import { VimEngine } from '../vim/index.js';
import type { VimMode } from '../vim/types.js';

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
  { cmd: '/thinking', desc: 'Toggle thinking mode' },
  { cmd: '/voice', desc: 'Toggle voice input' },
  { cmd: '/search', desc: 'Search conversation history' },
  { cmd: '/vim', desc: 'Toggle vim mode' },
  { cmd: '/task', desc: 'Task lifecycle' },
  { cmd: '/task new', desc: 'Create task' },
  { cmd: '/task verify', desc: 'Verify task' },
  { cmd: '/task close', desc: 'Close task' },
  { cmd: '/cron', desc: 'Scheduled tasks' },
  { cmd: '/cron add', desc: 'Create scheduled task' },
  { cmd: '/cron delete', desc: 'Delete scheduled task' },
  { cmd: '/vcr', desc: 'Session recording' },
  { cmd: '/vcr start', desc: 'Start recording' },
  { cmd: '/vcr stop', desc: 'Stop recording' },
  { cmd: '/vcr list', desc: 'List recordings' },
  { cmd: '/swarm', desc: 'List agent swarm' },
  { cmd: '/swarm create', desc: 'Spawn agent' },
  { cmd: '/swarm coordinate', desc: 'Auto-decompose task' },
  { cmd: '/swarm stopall', desc: 'Stop all agents' },
  { cmd: '/ide', desc: 'IDE connection status' },
  { cmd: '/hida', desc: 'Show task classification' },
  { cmd: '/checkpoint', desc: 'Session checkpoints' },
  { cmd: '/checkpoint save', desc: 'Save checkpoint' },
  { cmd: '/checkpoint resume', desc: 'Resume checkpoint' },
  { cmd: '/memory consolidate', desc: 'Consolidate memory' },
  { cmd: '/memory history', desc: 'Memory history' },
  { cmd: '/lsp', desc: 'LSP status' },
  { cmd: '/cancel', desc: 'Cancel generation' },
  { cmd: '/exit', desc: 'Quit' },
  { cmd: '/quit', desc: 'Quit' },
];

interface InputBarProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
  vimEnabled?: boolean;
  onVimModeChange?: (mode: VimMode) => void;
}

export function InputBar({ onSubmit, disabled, vimEnabled, onVimModeChange }: InputBarProps) {
  const [value, setValue] = useState('');
  const [selectedHint, setSelectedHint] = useState(0);
  const engineRef = useRef<VimEngine>(new VimEngine(''));
  const [vimMode, setVimMode] = useState<VimMode>('insert');

  const suggestions = useMemo(() => {
    if (!value.startsWith('/') || value.includes(' ') && !value.startsWith('/skill ') && !value.startsWith('/mcp ') && !value.startsWith('/plugin ') && !value.startsWith('/memory ') && !value.startsWith('/session ')) {
      return [];
    }
    return SLASH_COMMANDS.filter(c => c.cmd.startsWith(value.toLowerCase()));
  }, [value]);

  useInput((input, key) => {
    // Vim mode key handling — intercept in NORMAL mode
    if (vimEnabled && vimMode === 'normal') {
      if (key.escape || input === '\x1b') {
        // Already in normal, stay
        return;
      }
      // Feed key to engine
      engineRef.current.feedKey(input);
      const newMode = engineRef.current.mode;
      const newBuffer = engineRef.current.buffer;
      setValue(newBuffer);
      if (newMode !== vimMode) {
        setVimMode(newMode);
        onVimModeChange?.(newMode);
      }
      return;
    }

    // Escape in insert mode — switch to normal if vim enabled
    if (vimEnabled && (key.escape || input === '\x1b')) {
      engineRef.current.setBuffer(value);
      engineRef.current.feedKey('\x1b');
      const newMode = engineRef.current.mode;
      setVimMode(newMode);
      onVimModeChange?.(newMode);
      return;
    }

    if (suggestions.length > 0) {
      if (key.downArrow) {
        setSelectedHint(s => Math.min(s + 1, suggestions.length - 1));
      } else if (key.upArrow) {
        setSelectedHint(s => Math.max(s - 1, 0));
      } else if (input === '\t' || key.tab || key.rightArrow) {
        setValue(suggestions[selectedHint].cmd);
        setSelectedHint(0);
      } else if (key.return && selectedHint >= 0) {
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
    if (vimEnabled) {
      engineRef.current.setBuffer(newValue);
    }
  };

  const handleSubmit = (text: string) => {
    if (suggestions.length > 0) {
      const cmd = suggestions[selectedHint].cmd.trim();
      onSubmit(cmd);
      setValue('');
      setSelectedHint(0);
    } else if (text.trim()) {
      onSubmit(text.trim());
      setValue('');
      setSelectedHint(0);
      if (vimEnabled) {
        engineRef.current.setBuffer('');
        setVimMode('insert');
        onVimModeChange?.('insert');
      }
    }
  };

  const modeIndicator = vimEnabled && vimMode === 'normal' ? '[N] ' : '';

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
        {modeIndicator ? <Text color="yellow" bold>{modeIndicator}</Text> : null}
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
