import React from 'react';

interface ToolOutputProps {
  toolCall: string;
}

const ToolOutput: React.FC<ToolOutputProps> = ({ toolCall }) => {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-frost">Show Tool Output</summary>
      <pre className="p-4 bg-bg-surface rounded overflow-x-auto">
        {toolCall}
      </pre>
    </details>
  );
};

export default ToolOutput;