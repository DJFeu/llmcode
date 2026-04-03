import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';

interface ActionSelectProps {
  title: string;
  actions: Array<{ id: string; label: string }>;
  onSelect: (actionId: string) => void;
  onCancel: () => void;
}

export function ActionSelect({ title, actions, onSelect, onCancel }: ActionSelectProps) {
  const [cursor, setCursor] = useState(0);

  useInput((input, key) => {
    if (key.downArrow) {
      setCursor(c => Math.min(c + 1, actions.length - 1));
    } else if (key.upArrow) {
      setCursor(c => Math.max(c - 1, 0));
    } else if (key.return) {
      onSelect(actions[cursor].id);
    } else if (key.escape) {
      onCancel();
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginTop={1}>
      <Text bold color="cyan">{title}</Text>
      {actions.map((a, i) => (
        <Text key={a.id}>
          <Text color="cyan">{i === cursor ? ' ❯ ' : '   '}</Text>
          <Text bold={i === cursor}>{a.label}</Text>
        </Text>
      ))}
    </Box>
  );
}
