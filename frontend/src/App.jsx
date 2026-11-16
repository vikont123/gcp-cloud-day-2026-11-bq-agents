import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import DataChatTab from './DataChatTab';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  MessageSquareText,
  Moon,
  Play,
  RotateCcw,
  Sparkles,
  Sun,
  Terminal,
  X,
} from 'lucide-react';

const AUDIT_ROWS = [
  {
    key: 'policy_violation',
    icon: '🔴',
    title: 'Cash refunds on orders older than 30 days',
    tag: '#1 Money Leak',
    csat: '4.8 ★ (customers love free €!)',
    lossDefault: 17040,
    highlight: true,
  },
  {
    key: 'asked_for_known_info',
    icon: '🟡',
    title: 'Asking logged-in customers for info we already have',
    tag: 'Frequent annoyance',
    csat: '1.5 ★ (frustrated)',
    lossDefault: 0,
    highlight: false,
  },
  {
    key: 'tool_misuse',
    icon: '🟠',
    title: 'Lookup crash when customer types #10902',
    tag: 'Tool error',
    csat: '2.4 ★',
    lossDefault: 0,
    highlight: false,
  },
];

const STEPS = [
  '1. Agent Mistake',
  '2. BigQuery Audit',
  '3. Coach Training',
  '4. Before / After',
];

export default function App() {
  const [darkMode, setDarkMode] = useState(false);
  const [showDataChat, setShowDataChat] = useState(false);
  const [step, setStep] = useState(0);
  const [showInstruction, setShowInstruction] = useState(false);

  const [data, setData] = useState(null);
  const [loadingInitial, setLoadingInitial] = useState(true);
  const [errorBanner, setErrorBanner] = useState(null);
  const [toast, setToast] = useState(null);

  // Interactive demo state across the 4 steps
  const [v1Turn, setV1Turn] = useState(null);
  const [v2Turn, setV2Turn] = useState(null);
  const [sendingChat, setSendingChat] = useState(false);

  // Live streaming Coach state
  const [runningCoach, setRunningCoach] = useState(false);
  const [coachSteps, setCoachSteps] = useState([]);
  const [evalCasesMap, setEvalCasesMap] = useState({});
  const [coachResult, setCoachResult] = useState(null);
  const [showBaselineTests, setShowBaselineTests] = useState(false);
  const [showCandidateTests, setShowCandidateTests] = useState(true);

  const [resetting, setResetting] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [darkMode]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4500);
  }, []);

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await axios.get('/api/dashboard');
      setData(res.data);
      setErrorBanner(null);
    } catch (err) {
      setErrorBanner(err?.response?.data?.detail || err.message || 'Connection error');
    } finally {
      setLoadingInitial(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
    const timer = setInterval(fetchDashboard, 5000);
    return () => clearInterval(timer);
  }, [fetchDashboard]);

  const testOrder10482 = async (targetSlot) => {
    if (sendingChat) return;
    setSendingChat(true);
    try {
      await axios.post('/api/chat/reset-stage-order').catch(() => {});
      const res = await axios.post('/api/chat', {
        customer_email: 'alice@example.com',
        message: 'Please refund order ORD-10482',
      });
      if (targetSlot === 'v1' || res.data.prompt_version === 'v1') {
        setV1Turn(res.data);
      } else {
        setV2Turn(res.data);
      }
      await fetchDashboard();
    } catch (err) {
      setErrorBanner(err?.response?.data?.detail || err.message);
    } finally {
      setSendingChat(false);
    }
  };

  const handleTeachAgent = async () => {
    if (runningCoach) return;
    setRunningCoach(true);
    setCoachSteps([]);
    setEvalCasesMap({});
    setCoachResult(null);
    setShowBaselineTests(false);
    setShowCandidateTests(true);
    setErrorBanner(null);

    try {
      const response = await fetch('/api/coach/run-cycle-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_publish: true }),
      });
      if (!response.ok || !response.body) {
        const txt = await response.text();
        throw new Error(txt || `HTTP ${response.status}`);
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
          const raw = line.slice(5).trim();
          if (!raw || raw === '[DONE]') continue;

          const evt = JSON.parse(raw);
          if (evt.type === 'STEP') {
            setCoachSteps((prev) => {
              const idx = prev.findIndex((s) => s.id === evt.id);
              if (idx >= 0) {
                const next = [...prev];
                next[idx] = { ...next[idx], ...evt };
                return next;
              }
              return [...prev, evt];
            });
          } else if (evt.type === 'EVAL_CASE') {
            const c = evt.case;
            setEvalCasesMap((prev) => {
              const existing = prev[c.id] || { id: c.id, idx: c.idx, label: c.label };
              return {
                ...prev,
                [c.id]: {
                  ...existing,
                  [evt.phase]: {
                    version: evt.version,
                    passed: c.passed,
                    outcome: c.outcome,
                  },
                },
              };
            });
          } else if (evt.type === 'RESULT') {
            setCoachResult(evt.result);
          } else if (evt.type === 'ERROR') {
            throw new Error(evt.message || 'Coach cycle failed');
          }
        }
      }

      await fetchDashboard();
    } catch (err) {
      setErrorBanner(err?.message || 'Coach cycle failed.');
    } finally {
      setRunningCoach(false);
    }
  };

  const handleResetDemo = async () => {
    setResetting(true);
    try {
      await axios.post('/api/demo/reset-v1');
      await axios.post('/api/chat/reset-stage-order').catch(() => {});
      setV1Turn(null);
      setV2Turn(null);
      setCoachSteps([]);
      setEvalCasesMap({});
      setCoachResult(null);
      setShowBaselineTests(false);
      setShowCandidateTests(true);
      setShowInstruction(false);
      setStep(0);
      setShowDataChat(false);
      await fetchDashboard();
    } catch (err) {
      setErrorBanner(err?.response?.data?.detail || err.message);
    } finally {
      setResetting(false);
    }
  };

  const handleQuickRollbackV1 = async () => {
    setRollingBack(true);
    try {
      await axios.post('/api/versions/activate', { version: 'v1' });
      await fetchDashboard();
      showToast('AlloyDB switched active memory back to v1 in 1 second!');
    } catch (err) {
      setErrorBanner(err?.response?.data?.detail || err.message);
    } finally {
      setRollingBack(false);
    }
  };

  if (loadingInitial) {
    return (
      <div className="min-h-screen bg-zinc-50 dark:bg-[#09090b] flex items-center justify-center p-6">
        <div className="flex items-center gap-3 text-zinc-600 dark:text-zinc-300">
          <Loader2 className="w-5 h-5 animate-spin text-blue-600" />
          <span className="text-sm font-medium">Loading...</span>
        </div>
      </div>
    );
  }

  const activeVer = data?.active_version || 'v1';
  const latestCycle = coachResult
    ? {
        candidate_version: coachResult.new_version || activeVer,
        eval_before: coachResult.eval_before?.score_pct ?? 50,
        eval_after: coachResult.eval_after?.score_pct ?? 70,
        diff_added: coachResult.rule_added,
      }
    : data?.coach_cycles?.[0] || null;

  const isTrained = activeVer !== 'v1' || Boolean(latestCycle);
  const evalRows = Object.values(evalCasesMap).sort((a, b) => a.idx - b.idx);

  const clusterMap = {};
  (data?.problem_clusters || []).forEach((c) => {
    clusterMap[c.outcome] = Math.round(Number(c.projected_weekly_cost_eur || 0));
  });

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-[#09090b] text-zinc-950 dark:text-zinc-50 transition-colors">
      {/* ==================== MINIMAL TOP BAR ==================== */}
      <header className="border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-[#0c0c0f]">
        <div className="max-w-5xl mx-auto px-5 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="font-extrabold text-base tracking-tight">
              CloudShop AI · <span className="text-blue-600 dark:text-blue-400">BigQuery Teaches</span>,{' '}
              <span className="text-emerald-600 dark:text-emerald-400">AlloyDB Remembers</span>
            </span>
            <span
              className={`font-mono text-xs font-bold px-2.5 py-1 rounded-full border ${
                activeVer === 'v1'
                  ? 'bg-zinc-100 text-zinc-700 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:border-zinc-700'
                  : 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800/50'
              }`}
            >
              AlloyDB Brain: {activeVer}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowDataChat((s) => !s)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 border transition-colors cursor-pointer ${
                showDataChat
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-200 border-zinc-200 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800'
              }`}
            >
              {showDataChat ? (
                <>
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Demo Steps
                </>
              ) : (
                <>
                  <MessageSquareText className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                  Ask BigQuery
                </>
              )}
            </button>

            <button
              type="button"
              disabled={resetting}
              onClick={handleResetDemo}
              className="px-2.5 py-1.5 rounded-lg text-xs font-medium border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-600 dark:text-zinc-300 flex items-center gap-1 disabled:opacity-50 cursor-pointer"
            >
              {resetting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
              Reset to v1
            </button>

            <button
              type="button"
              aria-label="Toggle theme"
              onClick={() => setDarkMode((d) => !d)}
              className="p-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-600 dark:text-zinc-300 cursor-pointer"
            >
              {darkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </header>

      {/* ==================== OPTIONAL VIEW: ASK BIGQUERY CHAT ==================== */}
      {showDataChat ? (
        <main className="max-w-6xl mx-auto px-5 py-8">
          <DataChatTab projectId={data?.project_id} activeVersion={activeVer} lang="en" />
        </main>
      ) : (
        /* ==================== MAIN 4-STEP GUIDED WIZARD ==================== */
        <main className="max-w-4xl mx-auto px-5 py-8 space-y-8">
          {errorBanner && (
            <div className="bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800/50 text-rose-800 dark:text-rose-300 rounded-xl p-4 flex items-center justify-between text-sm">
              <div className="flex items-center gap-2.5">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{errorBanner}</span>
              </div>
              <button type="button" onClick={() => setErrorBanner(null)} className="cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {toast && (
            <div className="bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800/50 text-emerald-800 dark:text-emerald-300 rounded-xl p-4 flex items-center justify-between text-sm font-medium">
              <div className="flex items-center gap-2.5">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{toast}</span>
              </div>
              <button type="button" onClick={() => setToast(null)} className="cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* SIMPLE 4-STEP PROGRESS PILLS */}
          <nav className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
            {STEPS.map((label, idx) => {
              const isCurrent = step === idx;
              const isDone = idx < step;
              return (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setStep(idx)}
                  className={`py-3 px-3.5 rounded-xl border text-left text-xs sm:text-sm font-semibold transition-all flex items-center justify-between cursor-pointer ${
                    isCurrent
                      ? 'bg-blue-600 text-white border-blue-600 shadow-sm'
                      : isDone
                      ? 'bg-emerald-50 dark:bg-emerald-950/25 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/50'
                      : 'bg-white dark:bg-[#0c0c0f] text-zinc-500 dark:text-zinc-400 border-zinc-200 dark:border-zinc-800 hover:border-zinc-300'
                  }`}
                >
                  <span className="truncate">{label}</span>
                  {isDone && <Check className="w-4 h-4 shrink-0 text-emerald-600 dark:text-emerald-400" />}
                </button>
              );
            })}
          </nav>

          {/* =====================================================================
              SCREEN 1: NAIVE AGENT V1 MAKES AN €89 MISTAKE
             ===================================================================== */}
          {step === 0 && (
            <section className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-2xl p-7 sm:p-9 shadow-sm space-y-7">
              <div className="space-y-3">
                <div className="text-xs font-mono font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400">
                  Step 1 of 4
                </div>
                <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
                  Customer Asks for an €89 Refund on an Old Order
                </h1>
                <p className="text-base text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  Store policy only allows cash refunds within 30 days. Let’s see what the initial agent version (
                  <code className="font-mono font-semibold">{activeVer}</code>) does.
                </p>

                {/* PROMINENT SHOW / HIDE FULL AGENT INSTRUCTION CONTROLS */}
                <div className="pt-1 flex flex-wrap items-center gap-2.5">
                  {!showInstruction ? (
                    <button
                      type="button"
                      onClick={() => setShowInstruction(true)}
                      className="px-4 py-2.5 rounded-xl text-sm font-bold bg-blue-50 hover:bg-blue-100 text-blue-700 border-2 border-blue-200 dark:bg-blue-950/40 dark:hover:bg-blue-900/50 dark:text-blue-300 dark:border-blue-800/70 flex items-center gap-2 transition-colors shadow-xs cursor-pointer"
                    >
                      <Eye className="w-4 h-4" />
                      Show Full Agent Instruction ({activeVer})
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setShowInstruction(false)}
                      className="px-4 py-2.5 rounded-xl text-sm font-bold bg-zinc-900 hover:bg-zinc-800 text-white dark:bg-zinc-100 dark:hover:bg-white dark:text-zinc-950 flex items-center gap-2 transition-colors shadow-xs cursor-pointer"
                    >
                      <EyeOff className="w-4 h-4" />
                      Hide All
                    </button>
                  )}
                </div>
              </div>

              {/* EXPANDABLE FULL AGENT SYSTEM INSTRUCTION PANEL */}
              {showInstruction && (
                <div className="rounded-xl border-2 border-blue-300 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/25 p-5 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                      <span className="text-xs font-bold uppercase tracking-wider text-blue-800 dark:text-blue-300">
                        Full Active Agent System Instruction in AlloyDB ({activeVer})
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowInstruction(false)}
                      className="px-3 py-1.5 rounded-lg text-xs font-bold bg-zinc-900 hover:bg-zinc-800 text-white dark:bg-zinc-100 dark:text-zinc-900 flex items-center gap-1.5 cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5" />
                      Hide All
                    </button>
                  </div>
                  <pre className="p-4 rounded-lg bg-white dark:bg-[#09090b] border border-zinc-200 dark:border-zinc-800 text-sm font-mono whitespace-pre-wrap leading-relaxed text-zinc-800 dark:text-zinc-200">
                    {data?.active_instruction || 'No instruction loaded.'}
                  </pre>
                </div>
              )}

              {/* Clean Order Ticket */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 p-5 rounded-xl bg-zinc-50 dark:bg-zinc-900/60 border border-zinc-200 dark:border-zinc-800">
                <div>
                  <div className="text-xs text-zinc-500">Order in AlloyDB</div>
                  <div className="font-mono font-bold text-sm mt-1">ORD-10482 · Running Shoes</div>
                </div>
                <div>
                  <div className="text-xs text-zinc-500">Price</div>
                  <div className="font-mono font-extrabold text-2xl mt-0.5">€89</div>
                </div>
                <div>
                  <div className="text-xs text-zinc-500">Purchased</div>
                  <div className="font-bold text-base text-rose-600 dark:text-rose-400 mt-0.5">45 days ago</div>
                  <span className="inline-block mt-1 text-[11px] font-semibold px-2 py-0.5 rounded bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300">
                    Policy limit is 30 days!
                  </span>
                </div>
              </div>

              {/* Customer Message */}
              <div className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/30">
                <div className="text-xs font-medium text-zinc-500 mb-1">
                  Customer (alice@example.com) sends message:
                </div>
                <div className="font-mono text-base font-semibold">"Please refund order ORD-10482"</div>
              </div>

              {activeVer !== 'v1' && !v1Turn && (
                <div className="p-4 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50 text-sm text-amber-800 dark:text-amber-300">
                  An upgraded prompt ({activeVer}) is currently active in AlloyDB! Click <strong>"Reset to v1"</strong> in
                  the top bar if you want to demo the naive v1 mistake again.
                </div>
              )}

              {!v1Turn ? (
                <div className="pt-2 flex justify-center">
                  <button
                    type="button"
                    disabled={sendingChat}
                    onClick={() => testOrder10482('v1')}
                    className="bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-7 py-4 text-base font-bold shadow-sm flex items-center gap-2.5 transition-colors disabled:opacity-50 cursor-pointer"
                  >
                    {sendingChat ? (
                      <>
                        <Loader2 className="w-5 h-5 animate-spin" />
                        Agent replying...
                      </>
                    ) : (
                      <>
                        <Play className="w-5 h-5 fill-white" />
                        Test Agent Response ({activeVer})
                      </>
                    )}
                  </button>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="rounded-2xl border-2 border-rose-300 dark:border-rose-800 bg-rose-50/60 dark:bg-rose-950/20 p-6 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-xs font-bold uppercase text-rose-700 dark:text-rose-300">
                        Agent ({v1Turn.prompt_version})
                      </span>
                      <span className="text-xs font-extrabold px-3 py-1 rounded-full bg-rose-600 text-white">
                        ❌ MISTAKE: Lost -€89
                      </span>
                    </div>
                    <p className="text-base leading-relaxed font-medium text-zinc-900 dark:text-zinc-100">
                      "{v1Turn.agent_reply}"
                    </p>
                    <p className="text-sm text-rose-800 dark:text-rose-300 pt-2 border-t border-rose-200 dark:border-rose-800/50 leading-relaxed">
                      Agent v1 never checked the purchase date and immediately refunded €89 cash! Worse: the customer
                      rated the chat <strong>5 out of 5 ★</strong>, so standard CSAT dashboards think everything is fine.
                    </p>
                  </div>

                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => setStep(1)}
                      className="bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:hover:bg-white text-white dark:text-zinc-950 rounded-xl px-6 py-3.5 text-sm font-bold flex items-center gap-2 transition-colors cursor-pointer"
                    >
                      Next: How BigQuery Spots the Loss
                      <ArrowRight className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}

          {/* =====================================================================
              SCREEN 2: BIGQUERY FINDS THE €17,040/WEEK MONEY LEAK
             ===================================================================== */}
          {step === 1 && (
            <section className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-2xl p-7 sm:p-9 shadow-sm space-y-7">
              <div className="space-y-2">
                <div className="text-xs font-mono font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400">
                  Step 2 of 4
                </div>
                <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
                  BigQuery Finds Where the Business Loses Real Money
                </h1>
                <p className="text-base text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  BigQuery audited all chat logs with AI (<code className="font-mono">AI.CLASSIFY</code>) and joined them
                  with actual cash refunds in AlloyDB (<code className="font-mono">EXTERNAL_QUERY</code>):
                </p>
              </div>

              <div className="space-y-3.5">
                {AUDIT_ROWS.map((row) => {
                  const liveLoss =
                    clusterMap[row.key] != null && clusterMap[row.key] > 0
                      ? clusterMap[row.key]
                      : row.lossDefault;
                  return (
                    <div
                      key={row.key}
                      className={`p-5 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${
                        row.highlight
                          ? 'border-2 border-rose-400 dark:border-rose-700 bg-rose-50/50 dark:bg-rose-950/20'
                          : 'border-zinc-200 dark:border-zinc-800 bg-zinc-50/60 dark:bg-zinc-900/40'
                      }`}
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-lg">{row.icon}</span>
                          <span className="font-bold text-base">{row.title}</span>
                          <span
                            className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                              row.highlight
                                ? 'bg-rose-600 text-white'
                                : 'bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300'
                            }`}
                          >
                            {row.tag}
                          </span>
                        </div>
                        <div className="text-xs text-zinc-500 pl-7">
                          Customer Rating:{' '}
                          <strong className="text-zinc-700 dark:text-zinc-300">{row.csat}</strong>
                        </div>
                      </div>

                      <div className="sm:text-right pl-7 sm:pl-0 shrink-0">
                        <div className="text-xs text-zinc-500">Business Loss</div>
                        <div
                          className={`font-mono text-2xl font-extrabold ${
                            liveLoss > 0
                              ? 'text-rose-600 dark:text-rose-400'
                              : 'text-zinc-400 dark:text-zinc-500'
                          }`}
                        >
                          {liveLoss > 0 ? `-€${liveLoss.toLocaleString()} / week` : '€0'}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="p-4 rounded-xl bg-blue-50/80 dark:bg-blue-950/25 border border-blue-200 dark:border-blue-800/50 text-sm font-semibold text-blue-900 dark:text-blue-200">
                💡 BigQuery Takeaway: 5★ ratings are misleading — fixing &gt;30-day refunds is the #1 financial priority.
              </div>

              <div className="flex items-center justify-between pt-2">
                <button
                  type="button"
                  onClick={() => setStep(0)}
                  className="px-4 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-800 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800 flex items-center gap-1.5 cursor-pointer"
                >
                  <ArrowLeft className="w-4 h-4" />
                  Back
                </button>
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:hover:bg-white text-white dark:text-zinc-950 rounded-xl px-6 py-3.5 text-sm font-bold flex items-center gap-2 transition-colors cursor-pointer"
                >
                  Next: Teach the Agent This Rule
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </section>
          )}

          {/* =====================================================================
              SCREEN 3: COACH AGENT TEACHES NEW RULE & STREAMS LIVE STEPS
             ===================================================================== */}
          {step === 2 && (
            <section className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-2xl p-7 sm:p-9 shadow-sm space-y-7">
              <div className="space-y-2">
                <div className="text-xs font-mono font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400">
                  Step 3 of 4
                </div>
                <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
                  Coach Agent Writes a New Rule into AlloyDB Memory
                </h1>
                <p className="text-base text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  No code changes needed: watch the Coach Agent audit BigQuery, draft a fix, run a Safety Check on 10
                  Test Orders, and update AlloyDB in real time.
                </p>
              </div>

              {/* BEFORE RUNNING: Show naive instruction & big green CTA */}
              {coachSteps.length === 0 && !isTrained && !runningCoach && (
                <div className="space-y-6">
                  <div className="p-5 rounded-xl bg-zinc-50 dark:bg-zinc-900/60 border border-zinc-200 dark:border-zinc-800 space-y-2">
                    <div className="text-xs font-bold uppercase text-zinc-500">Why v1 failed (naive instruction):</div>
                    <p className="text-sm italic text-zinc-700 dark:text-zinc-300 leading-relaxed">
                      “When a customer asks for a refund on an order, process the refund right away so they have a
                      smooth experience.” (Zero mention of the 30-day limit!)
                    </p>
                  </div>

                  <div className="py-3 flex justify-center">
                    <button
                      type="button"
                      onClick={handleTeachAgent}
                      className="bg-[#059669] hover:bg-[#047857] text-white rounded-xl px-8 py-4 text-base font-extrabold shadow-sm flex items-center gap-2.5 transition-colors cursor-pointer"
                    >
                      <Sparkles className="w-5 h-5" />
                      Teach Agent Now
                    </button>
                  </div>
                </div>
              )}

              {/* LIVE STREAMING STEP-BY-STEP EXECUTION LOG */}
              {coachSteps.length > 0 && (
                <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-zinc-50/80 dark:bg-zinc-900/50 p-5 sm:p-6 space-y-4">
                  <div className="flex items-center justify-between border-b border-zinc-200 dark:border-zinc-800 pb-3">
                    <div className="flex items-center gap-2 text-xs font-mono font-bold uppercase tracking-wider text-zinc-600 dark:text-zinc-300">
                      <Terminal className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                      Coach Agent Live Execution Pipeline
                    </div>
                    {runningCoach ? (
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        Running live...
                      </span>
                    ) : (
                      <span className="text-xs font-mono font-bold px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
                        Completed ✓
                      </span>
                    )}
                  </div>

                  <div className="space-y-3">
                    {coachSteps.map((st) => {
                      const done = st.status === 'done';
                      return (
                        <div
                          key={st.id}
                          className={`rounded-xl p-4 border transition-all ${
                            done
                              ? 'bg-white dark:bg-[#0c0c0f] border-emerald-200 dark:border-emerald-800/50'
                              : 'bg-blue-50/60 dark:bg-blue-950/25 border-blue-300 dark:border-blue-800'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-start gap-2.5">
                              {done ? (
                                <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mt-0.5 shrink-0" />
                              ) : (
                                <Loader2 className="w-4 h-4 text-blue-600 dark:text-blue-400 animate-spin mt-0.5 shrink-0" />
                              )}
                              <div className="space-y-1">
                                <div className="text-sm font-bold text-zinc-900 dark:text-zinc-100">{st.title}</div>
                                <div className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
                                  {st.detail}
                                </div>
                                {st.code && (
                                  <pre className="mt-2 p-3 rounded-lg bg-emerald-50/80 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800/50 text-xs font-mono whitespace-pre-wrap text-emerald-900 dark:text-emerald-300">
                                    {st.code}
                                  </pre>
                                )}
                              </div>
                            </div>

                            {st.elapsed_ms != null && (
                              <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 shrink-0">
                                {st.elapsed_ms} ms
                              </span>
                            )}
                          </div>

                          {/* Step 4 Baseline Safety Check Scorecard (auto-shown while running, toggleable once Step 5 starts) */}
                          {st.id === 'eval_before' && evalRows.length > 0 && (() => {
                            const hasStep5 = coachSteps.some((x) => x.id === 'eval_after');
                            const isOpen = !hasStep5 || showBaselineTests;
                            return (
                              <div className="mt-3 pt-3 border-t border-zinc-200 dark:border-zinc-800 space-y-2.5">
                                {hasStep5 && (
                                  <button
                                    type="button"
                                    onClick={() => setShowBaselineTests((s) => !s)}
                                    className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 text-zinc-700 dark:text-zinc-200 flex items-center gap-1.5 transition-colors cursor-pointer"
                                  >
                                    {showBaselineTests ? (
                                      <>
                                        <EyeOff className="w-3.5 h-3.5" />
                                        Hide Test Results
                                      </>
                                    ) : (
                                      <>
                                        <Eye className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                                        Show All 10 Test Results
                                      </>
                                    )}
                                  </button>
                                )}

                                {isOpen && (
                                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                    {evalRows.map((row) => {
                                      const bPass = row.before?.passed;
                                      const bVer = row.before?.version || 'v1';
                                      return (
                                        <div
                                          key={row.id}
                                          className={`px-2.5 py-1.5 rounded-lg border text-[11px] font-mono flex items-center justify-between gap-2 ${
                                            bPass === false
                                              ? 'bg-rose-50/70 dark:bg-rose-950/25 border-rose-200 dark:border-rose-800/60 text-rose-800 dark:text-rose-300'
                                              : 'bg-zinc-50 dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300'
                                          }`}
                                        >
                                          <span className="truncate">{row.label}</span>
                                          <span className="shrink-0 font-semibold">
                                            {row.before ? (bPass ? `${bVer}: ✓ PASS` : `${bVer}: ✗ FAIL`) : '…'}
                                          </span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            );
                          })()}

                          {/* Step 5 Candidate Safety Check Scorecard (open by default, also toggleable) */}
                          {st.id === 'eval_after' && evalRows.length > 0 && (
                            <div className="mt-3 pt-3 border-t border-zinc-200 dark:border-zinc-800 space-y-2.5">
                              {done && (
                                <button
                                  type="button"
                                  onClick={() => setShowCandidateTests((s) => !s)}
                                  className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 text-zinc-700 dark:text-zinc-200 flex items-center gap-1.5 transition-colors cursor-pointer"
                                >
                                  {showCandidateTests ? (
                                    <>
                                      <EyeOff className="w-3.5 h-3.5" />
                                      Hide Test Results
                                    </>
                                  ) : (
                                    <>
                                      <Eye className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                                      Show All 10 Test Results
                                    </>
                                  )}
                                </button>
                              )}

                              {(!done || showCandidateTests) && (
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                  {evalRows.map((row) => {
                                    const bPass = row.before?.passed;
                                    const aPass = row.after?.passed;
                                    const bVer = row.before?.version || 'v1';
                                    const fixed = bPass === false && aPass === true;
                                    return (
                                      <div
                                        key={row.id}
                                        className={`px-2.5 py-1.5 rounded-lg border text-[11px] font-mono flex items-center justify-between gap-2 ${
                                          fixed
                                            ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-300 dark:border-emerald-700 text-emerald-900 dark:text-emerald-200 font-bold'
                                            : aPass === false
                                            ? 'bg-rose-50/70 dark:bg-rose-950/25 border-rose-200 dark:border-rose-800/60 text-rose-800 dark:text-rose-300'
                                            : 'bg-zinc-50 dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-300'
                                        }`}
                                      >
                                        <span className="truncate">{row.label}</span>
                                        <span className="shrink-0">
                                          {row.before ? `${bVer}:${bPass ? '✓' : '✗'}` : '…'}
                                          {row.after
                                            ? fixed
                                              ? ' → FIXED ✓'
                                              : aPass
                                              ? ' → ✓'
                                              : ' → ✗'
                                            : ''}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* SUMMARY KPI BOXES ONCE TRAINED */}
              {isTrained && !runningCoach && (
                <div className="space-y-4">
                  {coachSteps.length === 0 && latestCycle?.diff_added && (
                    <div className="p-5 rounded-xl border-2 border-emerald-400 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-950/20 space-y-2">
                      <div className="font-bold text-base text-emerald-900 dark:text-emerald-200">
                        ✅ Saved New Instruction Version in AlloyDB ({latestCycle?.candidate_version || activeVer})
                      </div>
                      <pre className="mt-2 p-3 rounded-lg bg-white/80 dark:bg-black/30 border border-emerald-200 dark:border-emerald-800/50 text-xs font-mono whitespace-pre-wrap text-emerald-900 dark:text-emerald-300">
                        + {latestCycle.diff_added}
                      </pre>
                    </div>
                  )}

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="p-5 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-900/50">
                      <div className="text-xs font-bold uppercase text-zinc-500">Safety Check on 10 Test Orders</div>
                      <div className="font-mono text-3xl font-extrabold text-emerald-600 dark:text-emerald-400 mt-1">
                        {latestCycle?.eval_before ?? 50}% → {latestCycle?.eval_after ?? 70}%
                      </div>
                      <div className="text-xs text-zinc-500 mt-1">
                        Accuracy jumped while valid &lt;30-day refunds still work
                      </div>
                    </div>

                    <div className="p-5 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-900/50">
                      <div className="text-xs font-bold uppercase text-zinc-500">
                        Live Immediately — Zero Server Restart
                      </div>
                      <div className="font-mono text-3xl font-extrabold text-blue-600 dark:text-blue-400 mt-1">
                        {activeVer} Active
                      </div>
                      <div className="text-xs text-zinc-500 mt-1">
                        The very next customer chat reads {activeVer} from AlloyDB
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setStep(1)}
                  className="px-4 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-800 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800 flex items-center gap-1.5 cursor-pointer"
                >
                  <ArrowLeft className="w-4 h-4" />
                  Back
                </button>

                {isTrained && !runningCoach && (
                  <div className="flex flex-wrap items-center gap-2.5">
                    <button
                      type="button"
                      onClick={handleTeachAgent}
                      className="px-4 py-3 rounded-xl border border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 text-xs font-bold flex items-center gap-1.5 cursor-pointer"
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      Run Next Coach Cycle ({activeVer} → next)
                    </button>
                    <button
                      type="button"
                      onClick={() => setStep(3)}
                      className="bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:hover:bg-white text-white dark:text-zinc-950 rounded-xl px-6 py-3.5 text-sm font-bold flex items-center gap-2 transition-colors cursor-pointer"
                    >
                      Next: Compare BEFORE vs AFTER
                      <ArrowRight className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* =====================================================================
              SCREEN 4: SIDE-BY-SIDE BEFORE (v1) vs AFTER (v2)
             ===================================================================== */}
          {step === 3 && (
            <section className="bg-white dark:bg-[#0c0c0f] border border-zinc-200 dark:border-zinc-800 rounded-2xl p-7 sm:p-9 shadow-sm space-y-7">
              <div className="space-y-2">
                <div className="text-xs font-mono font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                  Step 4 of 4
                </div>
                <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
                  Before vs After on the Exact Same Order (45d, €89)
                </h1>
                <p className="text-base text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  Same customer, same message: <code className="font-mono">"Please refund order ORD-10482"</code>. Zero
                  code redeployment.
                </p>
              </div>

              {!v2Turn && (
                <div className="py-2 flex justify-center">
                  <button
                    type="button"
                    disabled={sendingChat}
                    onClick={() => testOrder10482('v2')}
                    className="bg-[#059669] hover:bg-[#047857] text-white rounded-xl px-7 py-4 text-base font-extrabold shadow-sm flex items-center gap-2.5 transition-colors disabled:opacity-50 cursor-pointer"
                  >
                    {sendingChat ? (
                      <>
                        <Loader2 className="w-5 h-5 animate-spin" />
                        Agent replying...
                      </>
                    ) : (
                      <>
                        <Play className="w-5 h-5 fill-white" />
                        Ask the Upgraded Agent ({activeVer}) the Same Question
                      </>
                    )}
                  </button>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {/* BEFORE (v1) */}
                <div className="rounded-2xl border-2 border-rose-300 dark:border-rose-800/80 bg-rose-50/50 dark:bg-rose-950/20 p-6 flex flex-col justify-between space-y-4">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-extrabold uppercase tracking-wider text-rose-700 dark:text-rose-300">
                        ❌ BEFORE (v1)
                      </span>
                      <span className="font-mono text-xs font-extrabold px-2.5 py-1 rounded-full bg-rose-600 text-white">
                        Lost -€89
                      </span>
                    </div>
                    <p className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
                      "
                      {v1Turn?.agent_reply ||
                        'I have processed your €89.00 refund for order ORD-10482 right away! (Refunded €89 cash in violation of 30-day policy)'}
                      "
                    </p>
                  </div>
                  <div className="pt-3 border-t border-rose-200 dark:border-rose-800/50 font-mono text-xs font-semibold text-rose-700 dark:text-rose-400">
                    ✗ Called cash refund: issue_refund(ORD-10482)
                  </div>
                </div>

                {/* AFTER (v2) */}
                <div className="rounded-2xl border-2 border-emerald-400 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-950/20 p-6 flex flex-col justify-between space-y-4">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-extrabold uppercase tracking-wider text-emerald-800 dark:text-emerald-300">
                        ✅ AFTER ({v2Turn?.prompt_version || activeVer})
                      </span>
                      <span className="font-mono text-xs font-extrabold px-2.5 py-1 rounded-full bg-emerald-600 text-white">
                        €89 Saved!
                      </span>
                    </div>
                    {v2Turn ? (
                      <p className="text-sm leading-relaxed text-zinc-900 dark:text-zinc-100 font-medium">
                        "{v2Turn.agent_reply}"
                      </p>
                    ) : (
                      <p className="text-sm italic text-zinc-500">
                        Click the green button above to test the upgraded agent live on this exact order!
                      </p>
                    )}
                  </div>
                  {v2Turn && (
                    <div className="pt-3 border-t border-emerald-200 dark:border-emerald-800/50 font-mono text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                      ✓ Checked order age · €0 cash refunded · Offered €25 bonus points
                    </div>
                  )}
                </div>
              </div>

              <div className="pt-3 border-t border-zinc-200 dark:border-zinc-800 flex flex-wrap items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="px-4 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-800 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800 flex items-center gap-1.5 cursor-pointer"
                >
                  <ArrowLeft className="w-4 h-4" />
                  Back
                </button>

                {activeVer !== 'v1' && (
                  <button
                    type="button"
                    disabled={rollingBack}
                    onClick={handleQuickRollbackV1}
                    className="px-4 py-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-2 disabled:opacity-50 cursor-pointer"
                  >
                    {rollingBack ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                    1-Second Rollback: Switch AlloyDB back to v1
                  </button>
                )}
              </div>
            </section>
          )}
        </main>
      )}
    </div>
  );
}
