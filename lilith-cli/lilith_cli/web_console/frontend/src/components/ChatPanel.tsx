import React, { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../stores/chatStore';

const ROLE_STYLE: Record<string, string> = {
  user: 'text-frost',
  assistant: 'text-snow',
  error: 'text-ember',
};

const ChatPanel: React.FC = () => {
  const { messages, running, connected, connect, send } = useChatStore();
  const [input, setInput] = useState('');
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => connect(), [connect]);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [messages]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    send(input);
    setInput('');
  };

  return (
    <section className="w-[28rem] shrink-0 flex flex-col border-l border-steel p-3">
      <div className="flex-1 overflow-y-auto space-y-3" aria-live="polite">
        {messages.map((message) => (
          <article key={message.id} className={ROLE_STYLE[message.role]}>
            <p className="whitespace-pre-wrap">{message.content}</p>
            {message.toolErrors && message.toolErrors.length > 0 && (
              <details className="mt-1 text-sm">
                <summary className="cursor-pointer text-gold">
                  {message.toolErrors.length} herramienta(s) con error
                </summary>
                <pre>{message.toolErrors.join('\n')}</pre>
              </details>
            )}
          </article>
        ))}
        {running && <p className="text-steel">Lilith está trabajando…</p>}
        <div ref={bottom} />
      </div>
      <form onSubmit={handleSubmit} className="flex mt-3">
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={connected ? 'Escribe a Lilith' : 'Conectando…'}
          aria-label="Mensaje"
          className="flex-1 p-2 bg-bg-surface border border-steel rounded"
        />
        <button
          type="submit"
          disabled={!connected || running}
          className="ml-2 px-4 py-2 bg-amethyst text-snow rounded disabled:opacity-50"
        >
          Enviar
        </button>
      </form>
    </section>
  );
};

export default ChatPanel;
