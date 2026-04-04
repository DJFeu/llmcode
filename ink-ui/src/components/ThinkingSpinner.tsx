import React, { useState, useEffect } from 'react';
import { Text } from 'ink';
import Spinner from 'ink-spinner';

interface ThinkingSpinnerProps {
  hasContent?: boolean;
}

export function ThinkingSpinner({ hasContent }: ThinkingSpinnerProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(e => e + 0.1);
    }, 100);
    return () => clearInterval(interval);
  }, []);

  const label = hasContent ? 'Thinking…' : 'Waiting for model…';

  return (
    <Text>
      <Text color="blue"><Spinner type="dots" /></Text>
      <Text color="blue"> {label} </Text>
      <Text dimColor>({elapsed.toFixed(1)}s)</Text>
    </Text>
  );
}
