import React, { useState, useEffect } from "react";
import { Box, Text } from "ink";

interface VoiceIndicatorProps {
  recording: boolean;
}

const VoiceIndicator: React.FC<VoiceIndicatorProps> = ({ recording }) => {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!recording) {
      setElapsed(0);
      return;
    }

    const start = Date.now();
    const interval = setInterval(() => {
      setElapsed((Date.now() - start) / 1000);
    }, 100);

    return () => clearInterval(interval);
  }, [recording]);

  if (!recording) return null;

  return (
    <Box>
      <Text color="red" bold>
        ●
      </Text>
      <Text color="red"> Recording... </Text>
      <Text dimColor>{elapsed.toFixed(1)}s</Text>
    </Box>
  );
};

export default VoiceIndicator;
