import React, { useState, useRef, useEffect, Component } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
  Bot,
  User,
  Loader2,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Database,
  Code2,
  Copy,
  Check,
  RotateCcw,
  Sparkles,
} from 'lucide-react';

// Required ErrorBoundary to catch ReactMarkdown v10+ crash parsing errors
class MarkdownErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="text-rose-500 text-xs p-2 font-mono">
          Markdown render fallback: {this.state.error?.message}
        </div>
      );
    }
    return this.props.children;
  }
}

const COPY = {
  ru: {
    greeting:
      'Привет! Я **Gemini Data Analytics**, подключённый напрямую к 4 живым таблицам BigQuery (`agent_events`, `session_outcomes`, `csat_surveys` и 5-миллионной `historical_events_3m`). Спросите меня о чём угодно обычными словами — я сам напишу SQL-запрос к BigQuery и покажу таблицу с цифрами.',
    quickTitle: 'Готовые вопросы в 1 клик',
    resetBtn: 'Очистить чат',
    quickHint: 'Нажмите на готовый вопрос или напишите свой на русском или английском языке.',
    tablesTitle: 'Подключённые таблицы BigQuery',
    projectLabel: 'Проект GCP',
    mainTitle: 'Спросите BigQuery обычными словами (Gemini Data Analytics)',
    mainSub: 'В реальном времени показывает ход мысли, сгенерированный SQL-запрос и таблицу с ответом',
    viewSql: 'Показать ход рассуждений и SQL-запрос',
    analyzing: 'Анализирую схему BigQuery и пишу SQL...',
    execSql: 'Выполненный SQL в BigQuery',
    copied: 'Скопировано',
    copySql: 'Копировать SQL',
    runningSql: 'Выполняю SQL в BigQuery и готовлю ответ...',
    connecting: 'Подключаюсь к Gemini Data Analytics...',
    placeholder: 'Задайте вопрос к данным BigQuery простыми словами... (Enter — отправить)',
    askBtn: 'Спросить',
    questions: [
      {
        badge: 'Ловушка оценок 5★ vs Деньги (€)',
        label: 'Почему у нарушений правил высокая оценка клиентов, но огромные убытки в евро?',
        question:
          'Соедини session_outcomes и csat_surveys по session_id: покажи количество чатов, среднюю оценку CSAT и сумму убытков wrongful_refund_eur по каждому типу исхода (outcome) в виде markdown-таблицы. Ответь на русском языке.',
      },
      {
        badge: 'Масштаб: 5 000 000 событий',
        label: 'Сравни работу 3 агентов по всем 5 миллионам строк в historical_events_3m',
        question:
          'По всем 5 000 000 строк в historical_events_3m сравни агентов (cloudshop_support, returns_specialist, vip_concierge) по числу сессий, доле успешных решений, средней задержке latency_ms и числу ошибок TOOL_ERROR в markdown-таблице. Ответь на русском языке.',
      },
      {
        badge: 'Рост качества v1 → v2 → v3',
        label: 'Как выросла точность и сократились убытки между версиями промпта?',
        question:
          'Сравни версии prompt_version (v1, v2, v3) по таблицам session_outcomes и csat_surveys: покажи число сессий, % решённых чатов, средний CSAT и сумму wrongful_refund_eur в markdown-таблице. Ответь на русском языке.',
      },
      {
        badge: 'Живые события ADK',
        label: 'Какие типы событий сейчас записаны в таблице agent_events?',
        question:
          'Покажи в agent_events количество строк, среднюю задержку latency_ms и число ошибок по каждому event_type в markdown-таблице. Ответь на русском языке.',
      },
    ],
    tables: [
      {
        table: 'agent_analytics.agent_events',
        desc: 'Сырой журнал каждого шага агента (вопросы, вызовы функций, ошибки)',
      },
      {
        table: 'agent_analytics.session_outcomes',
        desc: 'Оценка каждого диалога ИИ-ревизором (решено / нарушение / ошибка)',
      },
      {
        table: 'business.csat_surveys',
        desc: 'Оценки покупателей (1–5★) и потерянные деньги на возвратах (€)',
      },
      {
        table: 'agent_analytics.historical_events_3m',
        desc: '5 000 000 событий за 3 месяца для проверки работы на больших объёмах',
      },
    ],
  },
  en: {
    greeting:
      'Hi! I am **Gemini Conversational Analytics**, connected live to your four BigQuery tables (`agent_events`, `session_outcomes`, `csat_surveys`, and the 5M-row `historical_events_3m`). Ask me anything in plain English or Russian — I will write and execute Standard SQL on BigQuery in real time.',
    quickTitle: '1-Click Stage Questions',
    resetBtn: 'Reset Chat',
    quickHint: 'Click any ready-made question or type any audience question in plain English or Russian.',
    tablesTitle: 'Connected BigQuery Tables',
    projectLabel: 'GCP Project',
    mainTitle: 'Ask BigQuery in Plain Language (Gemini Data Analytics)',
    mainSub: 'Streams live reasoning steps, generated Standard SQL, BigQuery results, and follow-up ideas',
    viewSql: 'View reasoning & generated BigQuery SQL',
    analyzing: 'Analyzing BigQuery schema & generating SQL...',
    execSql: 'Executed BigQuery SQL',
    copied: 'Copied',
    copySql: 'Copy SQL',
    runningSql: 'Running SQL on BigQuery & synthesizing answer...',
    connecting: 'Connecting to Gemini Data Analytics...',
    placeholder: 'Ask a plain-language question about your BigQuery data... (Enter to send)',
    askBtn: 'Ask BQ',
    questions: [
      {
        badge: '5★ CSAT vs € Loss Trap',
        label: 'Why do policy violations get high 5★ CSAT while losing €€€?',
        question:
          'Join session_outcomes and csat_surveys on session_id: show session count, average CSAT score, and total wrongful_refund_eur grouped by outcome in a markdown table.',
      },
      {
        badge: '5,000,000-Row Scale Scan',
        label: 'Compare all 3 agents across 5,000,000 events in historical_events_3m',
        question:
          'Across all 5,000,000 rows in historical_events_3m, compare agent (cloudshop_support, returns_specialist, vip_concierge) by unique sessions, resolution rate, avg latency_ms, and TOOL_ERROR count in a markdown table.',
      },
      {
        badge: 'Version Curve v1 → v2 → v3',
        label: 'Compare resolution rate, CSAT, and € lost across prompt versions',
        question:
          'Compare prompt_version (v1, v2, v3) across session_outcomes and csat_surveys by total sessions, resolution rate %, average CSAT score, and total wrongful_refund_eur.',
      },
      {
        badge: 'Live ADK Events',
        label: 'Show event counts and latency by event_type in agent_events',
        question:
          'In agent_events, show row count, average latency_ms, and error count for each event_type in a markdown table.',
      },
    ],
    tables: [
      {
        table: 'agent_analytics.agent_events',
        desc: 'Automatic step-by-step agent log from Google ADK plugin',
      },
      {
        table: 'agent_analytics.session_outcomes',
        desc: 'AI Judge diagnosis per chat & prompt_version (v1/v2/v3)',
      },
      {
        table: 'business.csat_surveys',
        desc: 'Customer 1–5★ ratings & wrongful € refunds',
      },
      {
        table: 'agent_analytics.historical_events_3m',
        desc: '5,000,000-row 90-day multi-agent benchmark dataset',
      },
    ],
  },
};

export default function DataChatTab({ projectId, activeVersion, lang = 'en' }) {
  const t = COPY[lang] || COPY.en;

  const makeGreeting = (langKey) => {
    const c = COPY[langKey] || COPY.en;
    return {
      role: 'model',
      content: c.greeting,
      thoughts: '',
      sqlQueries: [],
      suggestions: c.questions.slice(0, 3).map((q) => q.question),
    };
  };

  const [messages, setMessages] = useState([makeGreeting(lang)]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [expandedThoughts, setExpandedThoughts] = useState({});
  const [copiedSqlKey, setCopiedSqlKey] = useState(null);

  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  // Update initial greeting language if user hasn't chatted yet
  useEffect(() => {
    setMessages((prev) => {
      if (prev.length === 1 && prev[0].role === 'model' && !prev[0].thoughts) {
        return [makeGreeting(lang)];
      }
      return prev;
    });
  }, [lang]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  // Dynamic multiline textarea auto-resize
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = Math.min(scrollHeight, 132) + 'px';
      textareaRef.current.style.overflowY = scrollHeight > 132 ? 'auto' : 'hidden';
    }
  }, [input]);

  const toggleThought = (idx) => {
    setExpandedThoughts((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  const handleCopySql = (sql, key) => {
    navigator.clipboard.writeText(sql);
    setCopiedSqlKey(key);
    setTimeout(() => setCopiedSqlKey(null), 2000);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleResetChat = () => {
    if (isLoading) return;
    setMessages([makeGreeting(lang)]);
    setExpandedThoughts({});
    setInput('');
  };

  const handleSend = async (e, overrideText = null) => {
    e?.preventDefault();
    const textToSend = (overrideText ?? input).trim();
    if (!textToSend || isLoading) return;

    if (!overrideText) setInput('');
    setIsLoading(true);

    const userMsg = { role: 'user', content: textToSend };
    const currentMessages = [...messages, userMsg];
    const nextIdx = currentMessages.length;

    let thinking = '';
    let reply = '';
    let suggestions = [];
    let sqlQueries = [];

    setMessages([
      ...currentMessages,
      { role: 'model', content: '', thoughts: '', sqlQueries: [], suggestions: [] },
    ]);

    try {
      const historyPayload = currentMessages
        .slice(1, -1)
        .filter((m) => m.content && m.content.trim().length > 0)
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await fetch('/api/data-chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: `[Active AlloyDB Prompt Version: ${activeVersion || 'v1'}] ${textToSend}`,
          history: historyPayload,
        }),
      });

      if (!response.ok || !response.body) {
        const errText = await response.text();
        throw new Error(errText || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const dataStr = line.slice(5).trim();
          if (dataStr === '[DONE]') break;
          if (!dataStr) continue;

          try {
            const parsed = JSON.parse(dataStr);
            if (parsed.type === 'THOUGHT') {
              thinking += parsed.content + '\n';
            } else if (parsed.type === 'SQL') {
              if (parsed.content && !sqlQueries.includes(parsed.content)) {
                sqlQueries = [...sqlQueries, parsed.content];
              }
            } else if (parsed.type === 'SUGGESTION') {
              if (parsed.content && !suggestions.includes(parsed.content)) {
                suggestions = [...suggestions, parsed.content];
              }
            } else if (parsed.type === 'FINAL_RESPONSE') {
              reply += parsed.content;
            }

            setMessages((prev) => {
              const updated = [...prev];
              updated[nextIdx] = {
                role: 'model',
                content: reply,
                thoughts: thinking,
                sqlQueries,
                suggestions,
              };
              return updated;
            });
          } catch (parseErr) {
            console.error('SSE JSON parse error:', parseErr, dataStr);
          }
        }
      }
    } catch (err) {
      console.error('Data chat stream error:', err);
      setMessages((prev) => {
        const updated = [...prev];
        updated[nextIdx] = {
          role: 'model',
          content: `⚠️ **Connection Error**: ${err?.message || 'Unable to stream from Gemini Data Analytics API.'}`,
          thoughts: thinking,
          sqlQueries,
          suggestions: [],
        };
        return updated;
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
      {/* LEFT SIDEBAR: 1-Click Questions & Connected BigQuery Tables */}
      <div className="lg:col-span-4 space-y-5">
        <div className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-xl p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-blue-500" />
              <h3 className="font-bold text-sm text-zinc-950 dark:text-zinc-50">{t.quickTitle}</h3>
            </div>
            <button
              type="button"
              disabled={isLoading}
              onClick={handleResetChat}
              className="text-xs px-2.5 py-1 rounded-[6px] border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-600 dark:text-zinc-300 flex items-center gap-1.5 transition-colors disabled:opacity-50 cursor-pointer"
            >
              <RotateCcw className="w-3 h-3" />
              {t.resetBtn}
            </button>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{t.quickHint}</p>
          <div className="space-y-2.5">
            {t.questions.map((item, idx) => (
              <button
                key={idx}
                type="button"
                disabled={isLoading}
                onClick={() => handleSend(null, item.question)}
                className="w-full text-left p-3.5 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-900/50 hover:border-blue-400 dark:hover:border-blue-500/60 hover:bg-blue-50/40 dark:hover:bg-blue-950/20 transition-colors disabled:opacity-50 cursor-pointer"
              >
                <span className="inline-block text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-blue-100/80 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 mb-1.5">
                  {item.badge}
                </span>
                <div className="text-xs font-medium text-zinc-800 dark:text-zinc-200 leading-snug">
                  {item.label}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-xl p-5 shadow-sm space-y-3">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-emerald-500" />
            <h3 className="font-bold text-sm text-zinc-950 dark:text-zinc-50">{t.tablesTitle}</h3>
          </div>
          <div className="text-xs font-mono text-zinc-500 dark:text-zinc-400">
            {t.projectLabel}: {projectId || 'configured-gcp-project'}
          </div>
          <div className="space-y-2">
            {t.tables.map((tbl) => (
              <div
                key={tbl.table}
                className="p-2.5 rounded-lg bg-zinc-50 dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-zinc-800"
              >
                <div className="font-mono text-xs font-semibold text-blue-600 dark:text-blue-400">
                  {tbl.table}
                </div>
                <div className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{tbl.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* RIGHT MAIN PANEL: Conversational Analytics Streaming Chat */}
      <div className="lg:col-span-8 bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-xl shadow-sm flex flex-col h-[700px]">
        <div className="px-5 py-4 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-zinc-950 dark:text-zinc-50">{t.mainTitle}</h2>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                Live BigQuery SQL
              </span>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{t.mainSub}</p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {messages.map((m, i) => (
            <div
              key={i}
              className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}
            >
              <div
                className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center border ${
                  m.role === 'user'
                    ? 'bg-blue-600 border-blue-500 text-white'
                    : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-blue-600 dark:text-blue-400'
                }`}
              >
                {m.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>

              <div className="max-w-[86%] flex flex-col gap-2">
                {/* 1. Separate Reasoning / Thoughts & Generated SQL Box */}
                {m.role === 'model' &&
                  ((m.thoughts && m.thoughts.trim().length > 0) ||
                    (m.sqlQueries && m.sqlQueries.length > 0)) && (
                    <div className="bg-zinc-50 dark:bg-zinc-900/80 border border-zinc-200 dark:border-zinc-800 rounded-xl overflow-hidden shadow-xs">
                      <button
                        type="button"
                        onClick={() => toggleThought(i)}
                        className="w-full flex items-center gap-2 px-3.5 py-2 text-xs font-medium text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800/60 transition-colors cursor-pointer"
                      >
                        {expandedThoughts[i] ? (
                          <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />
                        )}
                        <BrainCircuit className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
                        <span className="truncate text-left flex-1">
                          {(() => {
                            if (m.content && m.content.length > 0) {
                              return `${t.viewSql}${
                                m.sqlQueries?.length ? ` (${m.sqlQueries.length} SQL)` : ''
                              }`;
                            }
                            const lines = (m.thoughts || '')
                              .split('\n')
                              .filter((l) => l.trim().length > 0);
                            return lines.length > 0 ? lines[lines.length - 1] : t.analyzing;
                          })()}
                        </span>
                      </button>

                      {expandedThoughts[i] && (
                        <div className="px-3.5 pb-3.5 pt-2.5 border-t border-zinc-200 dark:border-zinc-800 max-h-[300px] overflow-y-auto bg-zinc-100/70 dark:bg-black/30 text-[11px] font-mono leading-relaxed text-zinc-600 dark:text-zinc-400 space-y-2.5">
                          {(m.thoughts || '')
                            .split('\n')
                            .filter((l) => l.trim().length > 0)
                            .map((line, idx) => (
                              <div key={idx} className="flex gap-2 items-start">
                                <span className="text-blue-500 mt-[1px]">›</span>
                                <span className="break-words whitespace-pre-wrap">{line}</span>
                              </div>
                            ))}

                          {m.sqlQueries &&
                            m.sqlQueries.map((sql, sIdx) => {
                              const copyKey = `${i}-${sIdx}`;
                              return (
                                <div
                                  key={copyKey}
                                  className="mt-2 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-[#09090b] p-2.5"
                                >
                                  <div className="flex items-center justify-between mb-1.5">
                                    <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider font-semibold text-emerald-600 dark:text-emerald-400">
                                      <Code2 className="w-3 h-3" /> {t.execSql}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => handleCopySql(sql, copyKey)}
                                      className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 text-zinc-700 dark:text-zinc-300 cursor-pointer"
                                    >
                                      {copiedSqlKey === copyKey ? (
                                        <>
                                          <Check className="w-3 h-3 text-emerald-500" /> {t.copied}
                                        </>
                                      ) : (
                                        <>
                                          <Copy className="w-3 h-3" /> {t.copySql}
                                        </>
                                      )}
                                    </button>
                                  </div>
                                  <pre className="text-[11px] overflow-x-auto text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap">
                                    {sql}
                                  </pre>
                                </div>
                              );
                            })}
                        </div>
                      )}
                    </div>
                  )}

                {/* 2. Loading Pulse while waiting for FINAL_RESPONSE */}
                {m.role === 'model' && !m.content && (
                  <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400 italic ml-1 p-1">
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
                    {m.thoughts ? t.runningSql : t.connecting}
                  </div>
                )}

                {/* 3. Final Response Bubble (Markdown + GFM tables) */}
                {m.content && (
                  <div
                    className={`px-4 py-3 rounded-2xl text-sm leading-relaxed ${
                      m.role === 'user'
                        ? 'bg-blue-600 text-white rounded-tr-none shadow-xs'
                        : 'bg-zinc-100 dark:bg-[#16161a] text-zinc-800 dark:text-zinc-200 rounded-tl-none border border-zinc-200 dark:border-zinc-800 shadow-xs'
                    }`}
                  >
                    {m.role === 'model' ? (
                      <MarkdownErrorBoundary>
                        <div className="[&>p]:mb-2 [&>p:last-child]:mb-0 [&>ul]:list-disc [&>ul]:ml-5 [&>ul]:mb-2 [&>ol]:list-decimal [&>ol]:ml-5 [&>ol]:mb-2 [&>h3]:font-semibold [&>h3]:text-zinc-900 dark:[&>h3]:text-zinc-100 [&>h3]:mb-1 [&>h3]:mt-3 [&_code]:font-mono [&_code]:text-xs [&_code]:bg-black/5 dark:[&_code]:bg-white/10 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_table]:w-full [&_table]:my-2 [&_table]:text-xs [&_table]:border-collapse [&_th]:border [&_th]:border-zinc-300 dark:[&_th]:border-zinc-700 [&_th]:bg-zinc-200/60 dark:[&_th]:bg-zinc-800 [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left [&_td]:border [&_td]:border-zinc-300 dark:[&_td]:border-zinc-700 [&_td]:px-2.5 [&_td]:py-1.5 [&_td]:font-mono">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                        </div>
                      </MarkdownErrorBoundary>
                    ) : (
                      <div className="whitespace-pre-wrap">{m.content}</div>
                    )}
                  </div>
                )}

                {/* 4. Interactive Follow-up Suggestions */}
                {m.role === 'model' && m.suggestions && m.suggestions.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {m.suggestions.slice(0, 3).map((s, idx) => (
                      <button
                        key={idx}
                        type="button"
                        disabled={isLoading}
                        onClick={() => handleSend(null, s)}
                        className="text-left text-xs bg-blue-50 dark:bg-blue-950/30 hover:bg-blue-100 dark:hover:bg-blue-900/40 text-blue-700 dark:text-blue-300 px-3 py-1.5 rounded-lg border border-blue-200 dark:border-blue-800/40 transition-colors disabled:opacity-50 cursor-pointer"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <form
          onSubmit={handleSend}
          className="p-4 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-[#09090b] rounded-b-xl flex items-end gap-3"
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder={t.placeholder}
            rows={1}
            className="flex-1 bg-white dark:bg-[#141418] border border-zinc-300 dark:border-zinc-700 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 resize-none"
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-medium flex items-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer shrink-0"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {t.askBtn}
          </button>
        </form>
      </div>
    </div>
  );
}
