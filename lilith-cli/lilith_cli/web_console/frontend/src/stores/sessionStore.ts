import { create } from 'zustand';
import { apiGet } from '../api';

interface Health {
  version: string;
  workspace: string;
}

interface SessionState {
  health: Health | null;
  loadHealth: () => Promise<void>;
}

// /api/health is exempt from authentication, so the header can show the
// workspace even before a token is entered.
export const useSessionStore = create<SessionState>((set) => ({
  health: null,
  loadHealth: async () => {
    try {
      set({ health: await apiGet<Health>('/api/health/') });
    } catch {
      set({ health: null });
    }
  },
}));
