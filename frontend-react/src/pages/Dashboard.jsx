
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Activity, RefreshCw } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api' // Fixed port to 8000 based on uvicorn start

export default function Dashboard() {
    const navigate = useNavigate()
    const [positions, setPositions] = useState([])
    const [settings, setSettings] = useState({ mode: 'dry_run', allocation: 25, execution: 'auto', balance: 10000 })
    const [sentiment, setSentiment] = useState({ regime: 'RANGE', direction: 'NONE', confidence: 0 })
    const [botRunning, setBotRunning] = useState(false)
    const [dailyPnl, setDailyPnl] = useState([])
    const [lastUpdate, setLastUpdate] = useState('--:--:-- --')
    const [isLoading, setIsLoading] = useState(false)
    const [currentMode, setCurrentMode] = useState('dry_run')
    const [history, setHistory] = useState([])
    const [isFetching, setIsFetching] = useState(false)
    const [selectedExchange, setSelectedExchange] = useState('binance')

    // Get selected exchange on mount
    useEffect(() => {
        const exchange = localStorage.getItem('selectedExchange') || 'binance'
        setSelectedExchange(exchange)
    }, [])

    // Fetch data
    useEffect(() => {
        fetchData()
        const interval = setInterval(fetchData, 1000) // 1s for live updates
        return () => clearInterval(interval)
    }, [selectedExchange])

    const fetchData = async () => {
        setIsFetching(true)
        const exchange = localStorage.getItem('selectedExchange') || 'binance'
        try {
            const t = Date.now()
            const [posRes, settingsRes, sentimentRes, pnlRes, botRes, balRes, tradesRes] = await Promise.all([
                fetch(`${API_BASE}/${exchange}/positions?t=${t}`),
                fetch(`${API_BASE}/settings?t=${t}`),
                fetch(`${API_BASE}/sentiment?t=${t}`).catch(() => ({ ok: false })),
                fetch(`${API_BASE}/pnl/daily?t=${t}`).catch(() => ({ ok: false })),
                fetch(`${API_BASE}/${exchange}/status?t=${t}`),
                fetch(`${API_BASE}/${exchange}/balance?t=${t}`).catch(() => ({ ok: false })),
                fetch(`${API_BASE}/trades?t=${t}`).catch(() => ({ ok: false }))
            ])

            const positionsData = posRes.ok ? await posRes.json() : []
            const settingsData = settingsRes.ok ? await settingsRes.json() : {}
            const sentimentData = sentimentRes && sentimentRes.ok ? await sentimentRes.json() : {}
            const pnlData = pnlRes && pnlRes.ok ? await pnlRes.json() : []
            const botData = botRes.ok ? await botRes.json() : { running: false }
            const balData = balRes && balRes.ok ? await balRes.json() : { balance: 0 }
            const tradesData = tradesRes && tradesRes.ok ? await tradesRes.json() : []

            setPositions(positionsData)
            setSettings({ ...settingsData, balance: balData.balance }) // Dynamic balance
            setSentiment(sentimentData)
            setDailyPnl(pnlData)
            setBotRunning(botData.running)
            setHistory(tradesData)

            setLastUpdate(new Date().toLocaleTimeString('en-US', { hour12: true, hour: '2-digit', minute: '2-digit', second: '2-digit' }).toLowerCase())
        } catch (e) {
            console.error(e)
        } finally {
            setIsFetching(false)
        }
    }

    const startBot = async () => {
        const exchange = localStorage.getItem('selectedExchange') || 'binance'
        await fetch(`${API_BASE}/${exchange}/start`, { method: 'POST' })
        setBotRunning(true)
    }
    const stopBot = async () => {
        const exchange = localStorage.getItem('selectedExchange') || 'binance'
        await fetch(`${API_BASE}/${exchange}/stop`, { method: 'POST' })
        setBotRunning(false)
    }

    const updateSetting = async (key, value) => {
        await fetch(`${API_BASE}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [key]: value })
        })
        setSettings(prev => ({ ...prev, [key]: value }))
    }

    const switchMode = async (newMode) => {
        if (newMode === currentMode) return
        setIsLoading(true)
        setPositions([])
        try {
            await fetch(`${API_BASE}/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: newMode })
            })
            await new Promise(r => setTimeout(r, 500))
            await fetchData()
            setCurrentMode(newMode)
            setSettings(prev => ({ ...prev, mode: newMode }))
        } catch (e) {
            console.error(e)
        } finally {
            setIsLoading(false)
        }
    }

    const closePosition = async (symbol) => {
        if (!confirm(`Close position for ${symbol}?`)) return
        const exchange = localStorage.getItem('selectedExchange') || 'binance'
        await fetch(`${API_BASE}/${exchange}/positions/${symbol}/close`, { method: 'POST' })
        fetchData()
    }

    const todayPnl = dailyPnl.find(d => d.date === new Date().toISOString().split('T')[0])?.total_pnl || 0

    // Sentiment Gradient
    const sentimentGradient = `conic-gradient(var(--accent-green, #00d26a) 0deg 120deg, var(--accent-yellow, #ffa726) 120deg 240deg, var(--accent-red, #ff4757) 240deg 360deg)`;

    return (
        <div className="bg-background-dark text-white min-h-screen font-sans selection:bg-primary/30">

            <div className="max-w-[1400px] mx-auto p-5 flex flex-col min-h-screen">

                {/* Header */}
                <header className="flex justify-between items-center py-5 border-b border-border-color mb-8">
                    <div className="flex items-center gap-3">
                        <button onClick={() => navigate('/')} className="hover:bg-card-dark p-2 rounded-lg transition-colors text-text-secondary hover:text-white">
                            <ArrowLeft size={20} />
                        </button>
                        <div className="bg-primary/10 p-2 rounded-lg">
                            <span className="text-2xl">🦅</span>
                        </div>
                        <h1 className="text-2xl font-semibold tracking-tight">Sentinel <span className="text-primary font-bold">2.0 Pro</span></h1>
                    </div>

                    <div className="flex items-center gap-6">
                        <div className="flex items-center bg-card-dark p-1 rounded-lg border border-border-color">
                            <button onClick={() => switchMode('dry_run')} className={`px-4 py-1.5 text-xs font-medium rounded-md transition-all ${settings.mode === 'dry_run' ? 'bg-primary text-white shadow-lg shadow-primary/20' : 'text-text-secondary hover:bg-bg-hover'}`}>DRY-RUN</button>
                            <button onClick={() => { if (confirm('Switch to LIVE?')) switchMode('live') }} className={`px-4 py-1.5 text-xs font-medium rounded-md transition-all ${settings.mode === 'live' ? 'bg-accent-red text-white shadow-lg shadow-accent-red/20' : 'text-text-secondary hover:bg-bg-hover'}`}>LIVE</button>
                        </div>

                        <div className="flex gap-3">
                            <button
                                onClick={startBot}
                                disabled={botRunning}
                                className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold transition-all ${botRunning ? 'bg-bg-hover text-text-secondary cursor-not-allowed' : 'bg-accent-green hover:bg-accent-green/90 text-white shadow-lg shadow-accent-green/20'}`}
                            >
                                {botRunning ? 'Running...' : 'Start Bot'}
                            </button>

                            {botRunning && (
                                <button
                                    onClick={stopBot}
                                    className="bg-accent-red hover:bg-accent-red/90 text-white px-5 py-2.5 rounded-lg text-sm font-semibold shadow-lg shadow-accent-red/20 transition-all"
                                >
                                    Stop Bot
                                </button>
                            )}
                        </div>

                        <div className="flex items-center gap-2 px-3 py-1.5 bg-card-dark rounded-full border border-border-color">
                            <div className={`w-2 h-2 rounded-full ${botRunning ? 'bg-accent-green animate-pulse' : 'bg-text-secondary'}`}></div>
                            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">{botRunning ? 'Running' : 'Idle'}</span>
                        </div>
                    </div>
                </header>

                <main className="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1">

                    {/* Left Col */}
                    <div className="lg:col-span-7 flex flex-col gap-6">

                        {/* Positions Panel */}
                        <div className="bg-card-dark border border-border-color rounded-2xl p-6 relative overflow-hidden">
                            <div className="flex justify-between items-center mb-6">
                                <h2 className="text-lg font-semibold flex items-center gap-2">
                                    Open Positions
                                    <span className="bg-primary text-white text-[10px] px-2 py-0.5 rounded-full font-bold">{positions.length}</span>
                                </h2>

                                <div className="flex items-center gap-3">
                                    <span className="text-xs text-text-secondary uppercase">Allocation</span>
                                    <div className="flex gap-1">
                                        {[25, 50, 100].map(pct => (
                                            <button
                                                key={pct}
                                                onClick={() => updateSetting('allocation', pct)}
                                                className={`px-2 py-1 text-[10px] rounded border transition-all ${settings.allocation === pct ? 'bg-primary border-primary text-white' : 'border-border-color text-text-secondary hover:border-primary'}`}
                                            >{pct}%</button>
                                        ))}
                                    </div>
                                </div>
                            </div>

                            <div className="overflow-x-auto">
                                <table className="w-full text-left">
                                    <thead>
                                        <tr className="text-[10px] text-text-secondary uppercase tracking-wider border-b border-border-color">
                                            <th className="pb-3 pl-2">Symbol</th>
                                            <th className="pb-3">Side</th>
                                            <th className="pb-3">Size</th>
                                            <th className="pb-3">Entry</th>
                                            <th className="pb-3">Current</th>
                                            <th className="pb-3">P&L</th>
                                            <th className="pb-3 text-right pr-2">Action</th>
                                        </tr>
                                    </thead>
                                    <tbody className="text-sm">
                                        {positions.length === 0 ? (
                                            <tr><td colSpan="7" className="text-center py-12 text-text-secondary italic">No open positions</td></tr>
                                        ) : (
                                            positions.map(pos => (
                                                <tr key={pos.symbol} className="border-b border-border-color/50 last:border-0 hover:bg-bg-hover transition-colors">
                                                    <td className="py-4 pl-2 font-bold font-mono text-white">{pos.symbol}</td>
                                                    <td className="py-4">
                                                        <span className={`px-2 py-1 rounded text-[10px] font-bold ${pos.side === 'LONG' ? 'bg-accent-green/10 text-accent-green' : 'bg-accent-red/10 text-accent-red'}`}>{pos.side}</span>
                                                    </td>
                                                    <td className="py-4 text-text-secondary">
                                                        <span className="text-white">{pos.size?.toLocaleString()}</span> <span className="text-[10px]">({pos.leverage}x)</span>
                                                    </td>
                                                    <td className="py-4 font-mono text-text-secondary">${pos.entry_price?.toLocaleString()}</td>
                                                    <td className="py-4 font-mono text-primary">${pos.current_price?.toLocaleString()}</td>
                                                    <td className={`py-4 font-mono font-bold ${pos.pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                                                        {pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct?.toFixed(2)}%
                                                    </td>
                                                    <td className="py-4 text-right pr-2">
                                                        <button onClick={() => closePosition(pos.symbol)} className="text-[10px] border border-accent-red text-accent-red px-3 py-1 rounded hover:bg-accent-red hover:text-white transition-all uppercase font-medium">Close</button>
                                                    </td>
                                                </tr>
                                            ))
                                        )}
                                    </tbody>
                                </table>
                            </div>

                            <div className="mt-6 pt-6 border-t border-border-color grid grid-cols-2 gap-6">
                                <div>
                                    <span className="text-[10px] text-text-secondary uppercase tracking-wider block mb-1">Live Balance</span>
                                    <span className="text-2xl font-bold tracking-tight">${settings.balance?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                                </div>
                                <div className="text-right">
                                    <span className="text-[10px] text-text-secondary uppercase tracking-wider block mb-1">Today P&L</span>
                                    <span className={`text-2xl font-bold tracking-tight ${todayPnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                                        {todayPnl >= 0 ? '+' : ''}${todayPnl.toFixed(2)}
                                    </span>
                                </div>
                            </div>
                        </div>

                        {/* Trade History Panel */}
                        <div className="bg-card-dark border border-border-color rounded-2xl p-6">
                            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                                Trade History
                                <span className="bg-text-secondary/20 text-text-secondary text-[10px] px-2 py-0.5 rounded-full font-bold">{history.length}</span>
                            </h2>
                            <div className="overflow-x-auto max-h-48 overflow-y-auto">
                                <table className="w-full text-left text-sm">
                                    <thead>
                                        <tr className="text-[10px] text-text-secondary uppercase tracking-wider border-b border-border-color">
                                            <th className="pb-2">Symbol</th>
                                            <th className="pb-2">Side</th>
                                            <th className="pb-2">Entry</th>
                                            <th className="pb-2">Exit</th>
                                            <th className="pb-2">P&L</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {history.length === 0 ? (
                                            <tr><td colSpan="5" className="text-center py-6 text-text-secondary italic">No trades yet</td></tr>
                                        ) : (
                                            history.slice(0, 10).map((t, i) => (
                                                <tr key={i} className="border-b border-border-color/30 last:border-0">
                                                    <td className="py-2 font-mono font-bold">{t.symbol}</td>
                                                    <td className={`py-2 ${t.side === 'LONG' ? 'text-accent-green' : 'text-accent-red'}`}>{t.side}</td>
                                                    <td className="py-2 font-mono text-text-secondary">${t.entry_price?.toFixed(2)}</td>
                                                    <td className="py-2 font-mono text-text-secondary">${t.exit_price?.toFixed(2)}</td>
                                                    <td className={`py-2 font-mono font-bold ${t.pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                                                        {t.pnl >= 0 ? '+' : ''}${t.pnl?.toFixed(2)} ({t.roe_pct}%)
                                                    </td>
                                                </tr>
                                            ))
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                    </div>

                    {/* Right Col */}
                    <div className="lg:col-span-5 flex flex-col gap-6">

                        {/* Sentiment */}
                        <div className="bg-card-dark border border-border-color rounded-2xl p-8 flex flex-col items-center justify-center relative shadow-2xl">
                            <h2 className="text-xs font-bold text-text-secondary uppercase tracking-[0.2em] mb-8">Market Sentiment</h2>

                            {/* Gauge */}
                            <div className="relative w-32 h-32 flex items-center justify-center rounded-full mb-6" style={{ background: sentimentGradient }}>
                                <div className="absolute inset-[15%] bg-card-dark rounded-full flex items-center justify-center">
                                    {/* Inner content if needed */}
                                </div>
                            </div>

                            <div className={`text-4xl font-black italic tracking-tighter mb-2 ${sentiment.regime?.includes('BULL') ? 'text-accent-green' : sentiment.regime?.includes('BEAR') ? 'text-accent-red' : 'text-accent-yellow'}`}>
                                {sentiment.regime?.replace('__', ' ') || 'NEUTRAL'}
                            </div>

                            <div className="grid grid-cols-3 gap-2 w-full mt-8">
                                <div className="bg-background-dark/50 p-3 rounded-xl border border-border-color text-center">
                                    <div className="text-[10px] text-text-secondary uppercase mb-1">Regime</div>
                                    <div className="font-bold text-sm">{sentiment.regime?.split('_')[0]}</div>
                                </div>
                                <div className="bg-background-dark/50 p-3 rounded-xl border border-border-color text-center">
                                    <div className="text-[10px] text-text-secondary uppercase mb-1">Trend</div>
                                    <div className="font-bold text-sm">{sentiment.direction}</div>
                                </div>
                                <div className="bg-background-dark/50 p-3 rounded-xl border border-border-color text-center">
                                    <div className="text-[10px] text-text-secondary uppercase mb-1">Conf.</div>
                                    <div className="font-bold text-sm">{((sentiment.confidence || 0) * 100).toFixed(0)}%</div>
                                </div>
                            </div>
                        </div>

                    </div>

                </main>

                <footer className="mt-10 pt-8 border-t border-border-color flex justify-between items-center text-text-secondary text-[10px] uppercase font-medium">
                    <div className="text-right w-full">
                        <span className="text-2xl font-mono text-white mb-1 font-bold block">{lastUpdate.split(' ')[0]} <span className="text-sm">{lastUpdate.split(' ')[1]}</span></span>
                        <span>Last Update</span>
                    </div>
                </footer>

            </div>
        </div>
    )
}
