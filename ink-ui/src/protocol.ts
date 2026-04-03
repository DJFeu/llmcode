export interface DiffHunk {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  lines: string[];
}

export interface DiffData {
  hunks: DiffHunk[];
  additions: number;
  deletions: number;
}

// Messages from Python backend → Ink frontend
export type BackendMessage =
  | { type: 'welcome'; model: string; workspace: string; cwd: string; permissions: string; branch: string }
  | { type: 'ready' }
  | { type: 'user_echo'; text: string }
  | { type: 'thinking_start' }
  | { type: 'thinking_delta'; text: string }
  | { type: 'thinking_stop'; elapsed: number; tokens: number }
  | { type: 'text_delta'; text: string }
  | { type: 'text_done'; text: string }
  | { type: 'tool_start'; name: string; detail: string }
  | { type: 'tool_result'; name: string; output: string; isError: boolean; diff?: DiffData }
  | { type: 'tool_progress'; name: string; message: string }
  | { type: 'turn_done'; elapsed: number; tokens: number }
  | { type: 'permission_request'; toolName: string; args: string }
  | { type: 'marketplace_show'; title: string; items: MarketplaceItem[] }
  | { type: 'action_show'; name: string; actions: Array<{id: string; label: string}> }
  | { type: 'message'; text: string; style?: string }
  | { type: 'help'; commands: Array<{cmd: string; desc: string}> }
  | { type: 'error'; message: string }
  | CronListMessage

export interface MarketplaceItem {
  name: string;
  description: string;
  installed: boolean;
  index: number;
}

export interface CronTaskInfo {
  id: string;
  cron: string;
  prompt: string;
  recurring: boolean;
  permanent: boolean;
  created_at: string;
  last_fired_at: string | null;
}

export interface CronListMessage {
  type: "cron_list";
  tasks: CronTaskInfo[];
}

// Messages from Ink frontend → Python backend
export type FrontendMessage =
  | { type: 'user_input'; text: string }
  | { type: 'permission_response'; action: 'allow' | 'deny' | 'always' }
  | { type: 'marketplace_select'; index: number }
  | { type: 'marketplace_close' }
  | { type: 'action_select'; actionId: string }
  | { type: 'image_paste'; mediaType: string; data: string }
