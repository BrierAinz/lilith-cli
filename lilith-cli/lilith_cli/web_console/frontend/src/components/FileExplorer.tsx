import React from 'react';
import { useFileStore } from '../stores/fileStore';

interface FileNodeProps {
  file: FileNode;
}

const FileNode: React.FC<FileNodeProps> = ({ file }) => {
  const { openFile } = useFileStore();

  return (
    <div onClick={() => openFile(file.path)} className="cursor-pointer p-2 hover:bg-bg-surface">
      {file.name}
    </div>
  );
};

const FileExplorer: React.FC<{ files: FileNode[] }> = ({ files }) => {
  return (
    <div className="w-64 bg-bg-deep overflow-y-auto">
      {files.map((file) => (
        <FileNode key={file.path} file={file} />
      ))}
    </div>
  );
};

export default FileExplorer;