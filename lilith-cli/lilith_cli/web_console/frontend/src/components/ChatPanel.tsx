import React, { useState } from 'react';
import { useChat } from '../stores/chatStore';
import ToolOutput from './ToolOutput';

const ChatPanel: React.FC = () => {
  const { messages, sendMessage } = useChat();
  const [input, setInput] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
    setInput('');
  };

  return (
    <div className="flex flex-col h-full p-4">
      <div className="flex-grow overflow-y-auto">
        {messages.map((msg) => (
          <div key={msg.id} className="mb-4">
            <p>{msg.content}</p>
            {msg.toolCall && <ToolOutput toolCall={msg.toolCall} />}
          </div>
        ))}
      </div>
      <form onSubmit={handleSubmit} className="flex mt-4">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="flex-grow p-2 bg-bg-surface border border-steel rounded"
        />
        <button type="submit" className="ml-2 px-4 py-2 bg-amethyst text-snow rounded">Send</button>
      </form>
    </div>
  );
};

export default ChatPanel;