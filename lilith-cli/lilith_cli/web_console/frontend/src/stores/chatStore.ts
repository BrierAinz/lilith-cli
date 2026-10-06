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
let retryDelay = 1000;
let retryTimer: ReturnType<typeof setTimeout> | null = null;

// Close code the server uses when the token is missing or wrong.
const UNAUTHORIZED = 4401;
const MAX_RETRY_DELAY = 15000;

export const useChatStore = create<ChatState>((set, get) => {
  const push = (message: Omit<ChatMessage, 'id'>) =>
    set((state) => ({ messages: [...state.messages, { ...message, id: nextId++ }] }));

  return {
    messages: [],
    running: false,
    connected: false,
    connect: () => {
      if (socket && socket.readyState <= WebSocket.OPEN) return;
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      socket = openSocket('/api/chat');
      socket.onopen = () => {
        retryDelay = 1000;
        set({ connected: true });
      };
      socket.onclose = (event) => {
        set({ connected: false, running: false });
        if (event.code === UNAUTHORIZED) {
          push({ role: 'error', content: 'Token ausente o inválido.' });
          return;
        }
        // Network drops and server restarts (lilith web --dev) come back on their own.
        retryTimer = setTimeout(() => get().connect(), retryDelay);
        retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY);
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
