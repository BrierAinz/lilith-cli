import { useEffect, useState } from 'react';

function useWebSocket(url: string) {
  const [socket, setSocket] = useState<WebSocket | null>(null);
  const [lastMessage, setLastMessage] = useState<MessageEvent | null>(null);

  useEffect(() => {
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const endpoint = `${scheme}//${window.location.host}${url}`;
    const token = window.sessionStorage.getItem('lilith_auth_token');
    const ws = token
      ? new WebSocket(endpoint, ['lilith-auth', token])
      : new WebSocket(endpoint);
    setSocket(ws);

    ws.onmessage = (event) => setLastMessage(event);

    return () => ws.close();
  }, [url]);

  const sendMessage = (message: string) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(message);
    }
  };

  return { sendMessage, lastMessage };
}

export default useWebSocket;
