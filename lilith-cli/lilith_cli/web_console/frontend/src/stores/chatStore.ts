import create from 'zustand';
import { useEffect } from 'react';
import useWebSocket from '../hooks/useWebSocket';

interface ChatMessage {
  id: string;
  content: string;
  timestamp: Date;
}

interface ChatState {
  messages: ChatMessage[];
  addMessage: (message: ChatMessage) => void;
  connect: () => void;
}

const useChatStore = create<ChatState>((set) => ({
  messages: [],
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
  connect: () => {}
}));

export function useChat() {
  const chat = useChatStore();
  const { sendMessage, lastMessage } = useWebSocket('/api/chat');

  useEffect(() => {
    if (lastMessage) {
      chat.addMessage(JSON.parse(lastMessage.data));
    }
  }, [lastMessage]);

  return {
    ...chat,
    sendMessage
  };
}

export { useChatStore };
export default useChatStore;