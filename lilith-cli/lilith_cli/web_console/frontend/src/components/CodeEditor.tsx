import React from 'react';
import { useFileStore } from '../stores/fileStore';

// Read-only viewer: file changes go through the agent and its tool policy.
// A plain <pre> keeps the console local-first (no editor bundle or CDN).
const CodeEditor: React.FC = () => {
  const { currentFile, content } = useFileStore();

  if (!currentFile) {
    return (
      <section className="flex-1 flex items-center justify-center text-steel">
        Elige un archivo para verlo.
      </section>
    );
  }

  const lines = content.split('\n');
  return (
    <section className="flex-1 min-w-0 flex flex-col">
      <header className="px-3 py-2 text-sm text-frost border-b border-steel truncate">
        {currentFile}
      </header>
      <div className="flex-1 overflow-auto font-mono text-sm">
        <table className="border-collapse">
          <tbody>
            {lines.map((line, index) => (
              <tr key={index}>
                <td className="select-none text-right pr-4 pl-3 text-steel align-top">
                  {index + 1}
                </td>
                <td className="whitespace-pre pr-4">{line}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default CodeEditor;
