import React from 'react';
import { Box, Text, useInput } from 'ink';

interface PermissionDialogProps {
  toolName: string;
  args: string;
  onAction: (action: 'allow' | 'deny' | 'always') => void;
}

export function PermissionDialog({ toolName, args, onAction }: PermissionDialogProps) {
  useInput((input) => {
    if (input === 'y') onAction('allow');
    else if (input === 'n') onAction('deny');
    else if (input === 'a') onAction('always');
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text color="yellow" bold>⚠ Permission required</Text>
      <Text>  Tool: <Text bold>{toolName}</Text></Text>
      <Text>  Args: <Text dimColor>{args.slice(0, 80)}</Text></Text>
      <Text> </Text>
      <Text>  <Text bold>[y]</Text> Allow  <Text bold>[n]</Text> Deny  <Text bold>[a]</Text> Always allow</Text>
    </Box>
  );
}
