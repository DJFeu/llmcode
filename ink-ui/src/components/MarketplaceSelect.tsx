import React from 'react';
import { Box, Text } from 'ink';
import SelectInput from 'ink-select-input';
import type { MarketplaceItem } from '../protocol.js';

interface MarketplaceSelectProps {
  title: string;
  items: MarketplaceItem[];
  onSelect: (item: MarketplaceItem) => void;
  onCancel: () => void;
}

function ItemComponent({ isSelected, label }: { isSelected?: boolean; label: string }) {
  // Parse our label format: "● name  · description (installed)" or "○ name  · desc"
  const installed = label.startsWith('●');
  const parts = label.replace(/^[●○]\s*/, '').split('  · ');
  const name = parts[0] || '';
  const desc = parts[1] || '';

  return (
    <Text>
      <Text color={installed ? 'green' : 'gray'}>{installed ? '● ' : '○ '}</Text>
      <Text bold color={isSelected ? 'cyan' : undefined}>{name}</Text>
      <Text dimColor>  · {desc}</Text>
    </Text>
  );
}

function IndicatorComponent({ isSelected }: { isSelected?: boolean }) {
  return <Text color="cyan">{isSelected ? '❯ ' : '  '}</Text>;
}

export function MarketplaceSelect({ title, items, onSelect }: MarketplaceSelectProps) {
  const selectItems = items.map((item, i) => ({
    label: `${item.installed ? '●' : '○'} ${item.name}  · ${item.description}${item.installed ? ' (installed)' : ''}`,
    value: item,
    key: String(i),
  }));

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text bold color="yellow">{title}</Text>
      <Text dimColor>↑↓ navigate · Enter select · Esc cancel</Text>
      <Box flexDirection="column" marginTop={1}>
        <SelectInput
          items={selectItems}
          onSelect={(item) => onSelect(item.value as unknown as MarketplaceItem)}
          indicatorComponent={IndicatorComponent}
          itemComponent={ItemComponent}
        />
      </Box>
    </Box>
  );
}
