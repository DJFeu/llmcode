/**
 * SearchBar — Ctrl+F overlay for searching conversation history.
 *
 * When active (triggered by Ctrl+F) it renders a text-input field.
 * On submit it emits the query string via `onSearch`. Pressing Escape
 * or submitting an empty query closes the bar via `onClose`.
 */
import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';

interface SearchBarProps {
  /** Called with the trimmed query when the user presses Enter. */
  onSearch: (query: string) => void;
  /** Called when the search bar should be dismissed (Escape or empty submit). */
  onClose: () => void;
}

export function SearchBar({ onSearch, onClose }: SearchBarProps) {
  const [query, setQuery] = useState('');

  useInput((_input, key) => {
    if (key.escape) {
      onClose();
    }
  });

  function handleSubmit(value: string) {
    const trimmed = value.trim();
    if (!trimmed) {
      onClose();
      return;
    }
    onSearch(trimmed);
  }

  return (
    <Box borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text color="yellow" bold>
        {'Search '}
      </Text>
      <TextInput
        value={query}
        onChange={setQuery}
        onSubmit={handleSubmit}
        placeholder="type query, Enter to search, Esc to close"
      />
    </Box>
  );
}
