import React, { useEffect, useState } from 'react';
import ChatPanel from './components/ChatPanel';
import CodeEditor from './components/CodeEditor';
import FileExplorer from './components/FileExplorer';
import { setToken } from './api';
import { useChatStore } from './stores/chatStore';
import { useFileStore } from './stores/fileStore';
import { useSessionStore } from './stores/sessionStore';

const TokenForm: React.FC<{ onSaved: () => void }> = ({ onSaved }) => {
  const [value, setValue] = useState('');
  return (
    <form
      className="glass m-4 p-4 flex gap-2 items-center"
      onSubmit={(event) => {
        event.preventDefault();
        setToken(value);
        onSaved();
      }}
    >
      <label htmlFor="token" className="text-sm">
        Este servidor pide LILITH_AUTH_TOKEN:
      </label>
      <input
        id="token"
        type="password"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        className="flex-1 p-2 bg-bg-surface border border-steel rounded"
      />
      <button type="submit" className="px-4 py-2 bg-amethyst text-snow rounded">
        Guardar
      </button>
    </form>
  );
};

const App: React.FC = () => {
  const { loadFiles, unauthorized, error } = useFileStore();
  const { health, loadHealth } = useSessionStore();

  useEffect(() => {
    loadHealth();
    loadFiles();
  }, [loadHealth, loadFiles]);

  const retry = () => {
    loadFiles();
    useChatStore.getState().connect();
  };

  return (
    <div className="h-screen flex flex-col bg-bg-deep text-snow">
      <header className="flex justify-between items-center px-4 py-3 border-b border-steel">
        <span className="text-xl">ᛚ Lilith Web</span>
        <span className="text-sm text-steel" title={health?.workspace}>
          {health ? `${health.workspace} · v${health.version}` : 'Sin conexión'}
        </span>
      </header>
      {unauthorized && <TokenForm onSaved={retry} />}
      {error && !unauthorized && <p className="px-4 py-2 text-ember">{error}</p>}
      <main className="flex flex-1 min-h-0">
        <FileExplorer />
        <CodeEditor />
        <ChatPanel />
      </main>
    </div>
  );
};

export default App;
