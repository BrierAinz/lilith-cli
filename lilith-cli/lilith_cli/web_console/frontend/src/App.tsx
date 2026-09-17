import React from 'react';
import FileExplorer from './components/FileExplorer';
import CodeEditor from './components/CodeEditor';
import ChatPanel from './components/ChatPanel';
import Terminal from './components/Terminal';
import Sidebar from './components/Sidebar';
import { useFileStore } from './stores/fileStore';
import { useChatStore } from './stores/chatStore';
import { useSessionStore } from './stores/sessionStore';

const App: React.FC = () => {
  const { fileTree } = useFileStore();
  const { messages } = useChatStore();
  const { session } = useSessionStore();

  return (
    <div className="min-h-screen bg-deep text-snow">
      <header className="glass p-4 flex justify-between items-center">
        <span className="text-xl text-snow">ᛚ Lilith Web</span>
        <span className="text-sm text-steel">{session?.name || 'No session'}</span>
      </header>

      <div className="flex h-[calc(100vh-64px)] overflow-hidden">
        <Sidebar />
        <FileExplorer files={fileTree} />
        <CodeEditor />
        <ChatPanel messages={messages} />
        <Terminal />
      </div>
    </div>
  );
};

export default App;
