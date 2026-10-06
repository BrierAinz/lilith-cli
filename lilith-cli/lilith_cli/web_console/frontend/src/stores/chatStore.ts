import { create } from 'zustand';
import { openSocket } from '../api';

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant' | 'error';
  content: string;
  toolErrors?: string[];
}

// Messages the server sends on /api/chat (see web_console/api/chat.py).
type ServerEvent =
  | { type: 'status'; status: string }
  | { type: 'chat_result'; content: string; tool_errors?: string[]; cancelled?: boolean }
  | { type: 'error'; message: string };

interface ChatState {
  messages: ChatMessage[];
  running: boolean;
  connected: boolean;
  connect: () => void;
  send: (content: string) => void;
}

let socket: WebSocket | null = null;
let nextId = 1;

export const useChatStore = create<ChatState>((set, get) => {
  const push = (message: Omit<ChatMessage, 'id'>) =>
    set((state) => ({ messages: [...state.messages, { ...message, id: nextId++ }] }));

  return {
    messages: [],
    running: false,
    connected: false,
    connect: () => {
      if (socket && socket.readyState <= WebSocket.OPEN) return;
      socket = openSocket('/api/chat');
      socket.onopen = () => set({ connected: true });
      socket.onclose = (event) => {
        set({ connected: false, running: false });
        if (event.code === 4401) push({ role: 'error', content: 'Token ausente o inválido.' });
      };
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data) as ServerEvent;
        if (data.type === 'status') {
          set({ running: data.status === 'running' });
        } else if (data.type === 'chat_result') {
          set({ running: false });
          push({
            role: 'assistant',
            content: data.cancelled ? `${data.content}\n(cancelado)` : data.content,
            toolErrors: data.tool_errors ?? [],
          });
        } else {
          set({ running: false });
          push({ role: 'error', content: data.message });
        }
      };
    },
    send: (content) => {
      const text = content.trim();
      if (!text || get().running) return;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        push({ role: 'error', content: 'Sin conexión con el servidor.' });
        return;
      }
      push({ role: 'user', content: text });
      socket.send(JSON.stringify({ type: 'chat', content: text }));
    },
  };
});
