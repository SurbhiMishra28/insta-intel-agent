import { useEffect, useRef, useState } from 'react';
import { fmtCompact } from '../format.js';

const API_URL = import.meta.env.VITE_API_URL || ''; // '' = same-origin (vite dev proxy)

export default function ChatBox({ context }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]); // loading too: keep the 'thinking…' bubble in view

  const send = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', text: userMsg }]);

    setLoading(true);
    try {
      // Build context string from the insight data if available
      let ctx = '';
      if (context) {
        const c = context;
        ctx = [
          `Account: @${c.profile?.username || 'unknown'}`,
          `Followers: ${c.profile?.followers != null ? fmtCompact(c.profile.followers) : 'N/A'}`,
          `Engagement rate: ${c.metrics?.engagement_rate || 'N/A'}%`,
          `Avg likes/post: ${c.metrics?.avg_likes || 'N/A'}`,
          `Avg comments/post: ${c.metrics?.avg_comments != null ? c.metrics.avg_comments : 'N/A'}`,
          `Posting frequency: ${c.metrics?.posting_frequency_per_week || 'N/A'}/week`,
          `Best format: ${c.metrics?.best_content_type || 'N/A'}`,
          `Top hashtags: ${(c.metrics?.top_hashtags || []).join(', ') || 'none'}`,
          `Category: ${c.profile?.category || 'not set'}`,
          `Bio: ${c.profile?.bio || 'empty'}`,
          `Verified: ${c.profile?.is_verified || false}`,
          `AI summary: ${c.ai_summary || 'n/a'}`,
          `Strengths: ${(c.strengths || []).join('; ') || 'n/a'}`,
          `Weaknesses: ${(c.weaknesses || []).join('; ') || 'n/a'}`,
          `Recommendations: ${(c.recommendations || []).join('; ') || 'n/a'}`,
        ].join('\n');
      }

      const res = await fetch(`${API_URL}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMsg,
          username: context?.profile?.username || '',
          context: ctx || '',
        }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(`Chat request failed (${res.status}): ${detail.slice(0, 200)}`);
      }
      const data = await res.json();
      setMessages((prev) => [...prev, { role: 'bot', text: data.answer, llm: data.llm_used !== false }]);
    } catch (err) {
      console.error('[ChatBox] request failed:', err);
      setMessages((prev) => [
        ...prev,
        {
          role: 'bot',
          text: err.message?.startsWith('Chat request failed')
            ? `Sorry — the server rejected that request. ${err.message}`
            : 'Sorry, the chat couldn\'t reach the server. Is the backend running on port 8000?',
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <>
      {/* Chat toggle button */}
      <button
        onClick={() => setOpen(!open)}
        style={{
          position: 'fixed',
          bottom: 20,
          right: 20,
          width: 52,
          height: 52,
          borderRadius: '50%',
          background: 'var(--signal)',
          color: 'var(--ink)',
          border: 'none',
          cursor: 'pointer',
          boxShadow: '0 4px 16px rgba(198,255,78,0.35)',
          fontSize: 22,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1001,
          transition: 'transform 0.15s ease',
        }}
        aria-label="Open chat"
      >
        {open ? '✕' : '💬'}
      </button>

      {/* Chat panel */}
      {open && (
        <div style={{
          position: 'fixed',
          bottom: 84,
          right: 20,
          width: 340,
          maxHeight: 460,
          background: 'var(--panel)',
          border: '1px solid var(--hairline)',
          borderRadius: 10,
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          zIndex: 1001,
        }}>
          {/* Header */}
          <div style={{
            padding: '10px 14px',
            borderBottom: '1px solid var(--hairline)',
            fontFamily: 'var(--font-display)',
            fontWeight: 600,
            fontSize: 13.5,
            color: 'var(--signal)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexShrink: 0,
          }}>
            <span>AI Chat</span>
            {context && (
              <span style={{
                fontSize: 10, background: 'var(--signal-dim)', color: 'var(--ink)',
                padding: '2px 6px', borderRadius: 3, textTransform: 'uppercase',
                fontWeight: 700,
              }}>
                context
              </span>
            )}
          </div>

          {/* Messages */}
          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            minHeight: 0,
          }}>
            {messages.length === 0 && (
              <p style={{
                color: 'var(--paper-dim)',
                fontSize: 12.5,
                fontStyle: 'italic',
                margin: 0,
                textAlign: 'center',
                padding: '12px 0',
              }}>
                Ask anything about the account or Instagram growth.
              </p>
            )}
            {messages.map((m, i) => (
              <div
                key={`msg-${i}`}
                style={{
                  maxWidth: '88%',
                  alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                  background: m.role === 'user' ? 'var(--signal-dim)' : 'var(--panel-raised)',
                  color: m.role === 'user' ? 'var(--ink)' : 'var(--paper)',
                  padding: '8px 11px',
                  borderRadius: 8,
                  fontSize: 12.5,
                  lineHeight: 1.5,
                  wordBreak: 'break-word',
                }}
              >
                {m.text}
                {m.role === 'bot' && m.llm === false && (
                  <div style={{ marginTop: 4, fontSize: 10, fontStyle: 'italic', color: 'var(--paper-dim)', opacity: 0.75 }}>
                    ⚙ rule-based answer — AI provider unavailable (check /api/ai-status)
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div style={{
                alignSelf: 'flex-start',
                background: 'var(--panel-raised)',
                padding: '8px 11px',
                borderRadius: 8,
                color: 'var(--paper-dim)',
                fontSize: 12.5,
              }}>
                thinking…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div style={{
            display: 'flex',
            gap: 0,
            borderTop: '1px solid var(--hairline)',
            padding: '8px 10px',
            flexShrink: 0,
          }}>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question… or mention @handle / instagram URL for live data"
              rows={1}
              style={{
                flex: 1,
                background: 'transparent',
                border: 'none',
                outline: 'none',
                color: 'var(--paper)',
                fontFamily: 'var(--font-body)',
                fontSize: 13,
                resize: 'none',
                padding: '4px 0',
                lineHeight: 1.5,
                maxHeight: 80,
              }}
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              style={{
                background: input.trim() ? 'var(--signal)' : 'var(--hairline)',
                color: 'var(--ink)',
                border: 'none',
                borderRadius: 4,
                padding: '6px 10px',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: 16,
                flexShrink: 0,
                marginLeft: 6,
              }}
              aria-label="Send message"
            >
              ➤
            </button>
          </div>
        </div>
      )}
    </>
  );
}
