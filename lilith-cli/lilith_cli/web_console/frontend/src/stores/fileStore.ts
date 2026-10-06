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
    try {
      const encoded = path.split('/').map(encodeURIComponent).join('/');
      const { content } = await apiGet<{ content: string }>(`/api/files/${encoded}`);
      set({ currentFile: path, content, error: null });
    } catch (error) {
      set({ error: describe(error), unauthorized: error instanceof UnauthorizedError });
    }
  },
}));
