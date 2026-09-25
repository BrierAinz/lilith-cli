import { create } from 'zustand';

interface FileNode {
  name: string;
  path: string;
  children?: FileNode[];
}

interface FileState {
  fileTree: FileNode[];
  currentFile: string | null;
  unsavedChanges: boolean;
  loadFileTree: () => Promise<void>;
  openFile: (path: string) => void;
  saveFile: () => Promise<void>;
}

const useFileStore = create<FileState>((set) => ({
  fileTree: [],
  currentFile: null,
  unsavedChanges: false,
  loadFileTree: async () => {
    // Fetch file tree from API
    const response = await fetch('/api/files');
    const data = await response.json();
    set({ fileTree: data });
  },
  openFile: (path) => set({ currentFile: path, unsavedChanges: false }),
  saveFile: async () => {
    // Save current file via API
    set({ unsavedChanges: false });
  }
}));

export { useFileStore };
export default useFileStore;
