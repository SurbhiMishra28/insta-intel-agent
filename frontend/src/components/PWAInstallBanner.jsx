import { useEffect, useState } from 'react';

export default function PWAInstallBanner() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [show, setShow] = useState(false);

  useEffect(() => {
    const handler = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setShow(true);
    };
    window.addEventListener('beforeinstallprompt', handler);
    return () => window.removeEventListener('beforeinstallprompt', handler);
  }, []);

  const install = async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      setShow(false);
    }
    setDeferredPrompt(null);
  };

  if (!show) return null;

  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0,
      background: 'var(--signal)',
      color: '#fff',
      padding: '12px 16px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      zIndex: 1000,
      boxShadow: '0 -2px 12px rgba(0,0,0,0.4)',
      fontSize: 13.5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
        <span style={{ fontSize: 18 }}>⬇️</span>
        <span>Install InstaIQ for faster access</span>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={install}
          style={{
            background: '#fff', color: 'var(--signal)',
            border: 'none', borderRadius: 6,
            padding: '6px 16px', fontWeight: 700, fontSize: 12.5,
            cursor: 'pointer',
          }}
        >
          Install
        </button>
        <button
          onClick={() => { setShow(false); setDeferredPrompt(null); }}
          style={{
            background: 'transparent', color: '#fff',
            border: '1px solid rgba(255,255,255,0.4)',
            borderRadius: 6, padding: '6px 12px', fontSize: 12.5,
            cursor: 'pointer',
          }}
        >
          Maybe later
        </button>
      </div>
    </div>
  );
}
