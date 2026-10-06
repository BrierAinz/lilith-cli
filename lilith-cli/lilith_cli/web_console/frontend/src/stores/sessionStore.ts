import { create } from 'zustand';

interface SessionState {
  session: string | null;
  startSession: () => Promise<void>;
  endSession: () => Promise<void>;
}

const useSessionStore = create<SessionState>((set) => ({
  session: null,
  startSession: async () => {
    // Start session via API
    const response = await fetch('/api/session/start', { method: 'POST' });
    const data = await response.json();
    set({ session: data.sessionId });
  },
  endSession: async () => {
    // End session via API
    await fetch('/api/session/end', { method: 'POST' });
    set({ session: null });
  }
}));

export { useSessionStore };
export default useSessionStore;
