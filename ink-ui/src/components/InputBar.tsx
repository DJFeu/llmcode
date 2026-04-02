import React, { useState } from 'react';
import { Box, Text } from 'ink';
import TextInput from 'ink-text-input';

interface InputBarProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function InputBar({ onSubmit, disabled }: InputBarProps) {
  const [value, setValue] = useState('');

  const handleSubmit = (text: string) => {
    if (text.trim()) {
      onSubmit(text.trim());
      setValue('');
    }
  };

  return (
    <Box>
      <Text color="cyan" bold>❯ </Text>
      <TextInput
        value={value}
        onChange={setValue}
        onSubmit={handleSubmit}
        placeholder={disabled ? 'Thinking...' : 'Type a message... (/help for commands)'}
      />
    </Box>
  );
}
