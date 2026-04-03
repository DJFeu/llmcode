import React from "react";
import { Box, Text } from "ink";
import type { CronTaskInfo } from "../protocol.js";

export type CronTask = CronTaskInfo;

interface CronPanelProps {
  tasks: CronTask[];
}

export const CronPanel: React.FC<CronPanelProps> = ({ tasks }) => {
  if (tasks.length === 0) {
    return (
      <Box paddingX={1}>
        <Text dimColor>No scheduled tasks</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>Scheduled Tasks ({tasks.length})</Text>
      {tasks.map((task) => (
        <Box key={task.id} gap={1}>
          <Text color="cyan">{task.id}</Text>
          <Text color="yellow">{task.cron}</Text>
          <Text>{`"${task.prompt.length > 40 ? task.prompt.slice(0, 40) + "..." : task.prompt}"`}</Text>
          {task.recurring && <Text color="green">recurring</Text>}
          {task.permanent && <Text color="magenta">permanent</Text>}
          {task.last_fired_at && (
            <Text dimColor>last: {task.last_fired_at}</Text>
          )}
        </Box>
      ))}
    </Box>
  );
};
