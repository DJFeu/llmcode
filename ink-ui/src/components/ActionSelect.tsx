import React from 'react';
import { Box, Text } from 'ink';
import SelectInput from 'ink-select-input';

interface ActionSelectProps {
  title: string;
  actions: Array<{ id: string; label: string }>;
  onSelect: (actionId: string) => void;
  onCancel: () => void;
}

export function ActionSelect({ title, actions, onSelect }: ActionSelectProps) {
  const items = actions.map(a => ({
    label: a.label,
    value: a.id,
    key: a.id,
  }));

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginTop={1}>
      <Text bold color="cyan">{title}</Text>
      <SelectInput
        items={items}
        onSelect={(item) => onSelect(item.value as string)}
        indicatorComponent={({ isSelected }: { isSelected?: boolean }) => (
          <Text color="cyan">{isSelected ? '❯ ' : '  '}</Text>
        )}
      />
    </Box>
  );
}
