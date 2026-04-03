import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import type { MarketplaceItem } from '../protocol.js';

interface MarketplaceSelectProps {
  title: string;
  items: MarketplaceItem[];
  onSelect: (item: MarketplaceItem) => void;
  onCancel: () => void;
}

const VISIBLE = 15;

export function MarketplaceSelect({ title, items, onSelect, onCancel }: MarketplaceSelectProps) {
  const [cursor, setCursor] = useState(0);

  // Calculate visible window
  const start = Math.max(0, Math.min(cursor - Math.floor(VISIBLE / 2), items.length - VISIBLE));
  const end = Math.min(start + VISIBLE, items.length);
  const visibleItems = items.slice(start, end);

  useInput((input, key) => {
    if (key.downArrow) {
      setCursor(c => Math.min(c + 1, items.length - 1));
    } else if (key.upArrow) {
      setCursor(c => Math.max(c - 1, 0));
    } else if (key.return) {
      onSelect(items[cursor]);
    } else if (key.escape) {
      onCancel();
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text bold color="yellow">{title}</Text>
      <Text dimColor>↑↓ navigate · Enter select · Esc close</Text>
      <Text dimColor> </Text>
      {start > 0 && <Text dimColor>  ↑ {start} more above</Text>}
      {visibleItems.map((item, i) => {
        const globalIdx = start + i;
        const selected = globalIdx === cursor;
        const icon = item.installed ? '●' : '○';
        const iconColor = item.installed ? 'green' : 'gray';
        return (
          <Text key={globalIdx}>
            <Text color="cyan">{selected ? ' ❯ ' : '   '}</Text>
            <Text color={iconColor}>{icon} </Text>
            <Text bold={selected} color={selected ? 'cyan' : undefined}>{item.name}</Text>
            <Text dimColor>  · {item.description}</Text>
            {item.installed && <Text color="green"> (installed)</Text>}
          </Text>
        );
      })}
      {end < items.length && <Text dimColor>  ↓ {items.length - end} more below</Text>}
    </Box>
  );
}
