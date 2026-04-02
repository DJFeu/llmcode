// Messages from Python backend → Ink frontend
export type BackendMessage =
  | { type: 'welcome'; model: string; workspace: string; cwd: string; permissions: string; branch: string }
  | { type: 'ready' }
  | { type: 'user_echo'; text: string }
  | { type: 'thinking_start' }
  | { type: 'thinking_stop'; elapsed: number; tokens: number }
  | { type: 'text_delta'; text: string }
  | { type: 'text_done'; text: string }
  | { type: 'tool_start'; name: string; detail: string }
  | { type: 'tool_result'; name: string; output: string; isError: boolean }
  | { type: 'tool_progress'; name: string; message: string }
  | { type: 'turn_done'; elapsed: number; tokens: number }
  | { type: 'permission_request'; toolName: string; args: string }
  | { type: 'marketplace_show'; title: string; items: MarketplaceItem[] }
  | { type: 'message'; text: string; style?: string }
  | { type: 'help'; commands: Array<{cmd: string; desc: string}> }
  | { type: 'error'; message: string }

export interface MarketplaceItem {
  name: string;
  description: string;
  installed: boolean;
  index: number;
}

// Messages from Ink frontend → Python backend
export type FrontendMessage =
  | { type: 'user_input'; text: string }
  | { type: 'permission_response'; action: 'allow' | 'deny' | 'always' }
  | { type: 'marketplace_select'; index: number }
  | { type: 'marketplace_close' }
  | { type: 'image_paste'; mediaType: string; data: string }
