import React, { useMemo, useState } from 'react';
import { useFileStore } from '../stores/fileStore';

const FileExplorer: React.FC = () => {
  const { files, currentFile, openFile } = useFileStore();
  const [filter, setFilter] = useState('');

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? files.filter((file) => file.path.toLowerCase().includes(needle)) : files;
  }, [files, filter]);

  return (
    <aside className="w-72 shrink-0 flex flex-col bg-bg-deep border-r border-steel">
      <input
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="Filtrar archivos"
        aria-label="Filtrar archivos"
        className="m-2 p-2 bg-bg-surface border border-steel rounded text-sm"
      />
      <ul className="flex-1 overflow-y-auto text-sm">
        {visible.map((file) => (
          <li key={file.path}>
            <button
              type="button"
              onClick={() => openFile(file.path)}
              className={`w-full text-left px-3 py-1 truncate hover:bg-bg-surface ${
                file.path === currentFile ? 'bg-bg-elevated text-frost' : ''
              }`}
              title={file.path}
            >
              {file.path}
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
};

export default FileExplorer;
