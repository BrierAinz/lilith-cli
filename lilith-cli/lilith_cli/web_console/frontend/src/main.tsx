import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { captureTokenFromUrl } from './api';
import './styles/nordic-frost.css';

captureTokenFromUrl();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
