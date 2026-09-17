import React, { useEffect, useRef } from 'react';
import { Terminal as XTerm } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { WebLinksAddon } from 'xterm-addon-web-links';
import useWebSocket from '../hooks/useWebSocket';

const Terminal: React.FC = () => {
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const fitAddon = new FitAddon();
  const { sendMessage, lastMessage } = useWebSocket('/api/terminal');

  useEffect(() => {
    const term = new XTerm({
      cursorBlink: true,
      theme: {
        background: '#1a1b2e',
        foreground: '#c8d0e0'
      }
    });

    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());

    if (terminalRef.current) {
      term.open(terminalRef.current);
      fitAddon.fit();
      xtermRef.current = term;
    }

    return () => term.dispose();
  }, []);

  useEffect(() => {
    if (lastMessage && xtermRef.current) {
      xtermRef.current.write(lastMessage.data);
    }
  }, [lastMessage]);

  return <div ref={terminalRef} className="h-40" />;
};

export default Terminal;