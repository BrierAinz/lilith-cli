import { create } from 'zustand';
import { apiGet, UnauthorizedError } from '../api';

export interface WorkspaceFile {
  path: string;
  type: string;
}

interface FileState {
  files: WorkspaceFile[];
  currentFile: string | null;
  content: string;
  error: string | null;
  unauthorized: boolean;
  loadFiles: () => Promise<void>;
  openFile: (path: string) => Promise<void>;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Bumped on every openFile call; a response for an older call is dropped so
// a slow file cannot replace the one the user picked after it.
let openRequest = 0;

export const useFileStore = create<FileState>((set) => ({
  files: [],
  currentFile: null,
  content: '',
  error: null,
  unauthorized: false,
  loadFiles: async () => {
    try {
      const files = await apiGet<WorkspaceFile[]>('/api/files');
      set({ files, error: null, unauthorized: false });
    } catch (error) {
      set({ error: describe(error), unauthorized: error instanceof UnauthorizedError });
    }
  },
  openFile: async (path) => {
    const request = ++openRequest;
    try {
      const encoded = path.split('/').map(encodeURIComponent).join('/');
      const { content } = await apiGet<{ content: string }>(`/api/files/${encoded}`);
      if (request !== openRequest) return;
      set({ currentFile: path, content, error: null });
    } catch (error) {
      if (request !== openRequest) return;
      set({ error: describe(error), unauthorized: error instanceof UnauthorizedError });
    }
  },
}));
