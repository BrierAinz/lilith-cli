import React, { useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import { useFileStore } from '../stores/fileStore';

const CodeEditor: React.FC = () => {
  const editorRef = useRef(null);
  const { currentFile, unsavedChanges, saveFile } = useFileStore();

  const handleEditorDidMount = (editor: any) => {
    editorRef.current = editor;
  };

  useEffect(() => {
    if (editorRef.current && currentFile) {
      // Load file content into editor
    }
  }, [currentFile]);

  return (
    <div className="flex-grow">
      <Editor
        height="100%"
        theme="nordic-frost"
        language="typescript"
        onMount={handleEditorDidMount}
      />
    </div>
  );
};

export default CodeEditor;