import React, { useState, useEffect } from 'react';
import { Text } from 'ink';
import Spinner from 'ink-spinner';

export function ThinkingSpinner() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(e => e + 0.1);
    }, 100);
    return () => clearInterval(interval);
  }, []);

  return (
    <Text>
      <Text color="blue"><Spinner type="dots" /></Text>
      <Text color="blue"> Thinking… </Text>
      <Text dimColor>({elapsed.toFixed(1)}s)</Text>
    </Text>
  );
}
