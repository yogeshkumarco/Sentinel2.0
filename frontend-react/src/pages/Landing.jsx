
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Activity, Globe } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

export default function Landing() {
    const navigate = useNavigate()

    const selectExchange = (exchange) => {
        // Store selected exchange and navigate to dashboard
        // Bot will be started manually via the Start button in dashboard
        localStorage.setItem('selectedExchange', exchange)
        navigate('/dashboard')
    }

    return (
        <div className="bg-background-dark text-white min-h-screen flex flex-col font-sans selection:bg-primary/30 relative overflow-hidden">

            {/* Background Ambience */}
            <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none z-0">
                <div className="absolute -top-[20%] -left-[10%] w-[50%] h-[50%] bg-primary/5 rounded-full blur-[120px]"></div>
                <div className="absolute top-[40%] -right-[10%] w-[40%] h-[40%] bg-accent-blue/5 rounded-full blur-[100px]"></div>
            </div>

            <div className="relative z-10 max-w-5xl mx-auto w-full flex-1 flex flex-col justify-center items-center p-6">

                <div className="text-center mb-16 space-y-4">
                    <div className="inline-flex items-center justify-center p-3 bg-primary/10 rounded-2xl mb-4 border border-primary/20 shadow-lg shadow-primary/10">
                        <span className="text-4xl">🦅</span>
                    </div>
                    <h1 className="text-6xl font-black tracking-tight bg-gradient-to-br from-white via-white to-text-secondary bg-clip-text text-transparent">
                        Sentinel <span className="text-primary">2.0</span>
                    </h1>
                    <p className="text-xl text-text-secondary max-w-lg mx-auto leading-relaxed">
                        Advanced Autonomous Trading System with Multi-Exchange Support & Regime Detection
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8 w-full max-w-3xl">

                    {/* KuCoin Card */}
                    <button
                        onClick={() => selectExchange('kucoin')}
                        className="group relative bg-card-dark border border-border-color hover:border-accent-green p-8 rounded-3xl transition-all duration-300 hover:shadow-2xl hover:shadow-accent-green/10 text-left hover:-translate-y-1"
                    >
                        <div className="absolute top-6 right-6 p-2 bg-background-dark rounded-full border border-border-color group-hover:border-accent-green/50 transition-colors">
                            <ArrowRight className="w-5 h-5 text-text-secondary group-hover:text-accent-green" />
                        </div>

                        <div className="w-16 h-16 bg-[#00d26a]/10 rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-500">
                            <span className="text-3xl font-bold text-[#00d26a]">K</span>
                        </div>

                        <h3 className="text-2xl font-bold mb-2">KuCoin Futures</h3>
                        <p className="text-text-secondary text-sm mb-6">
                            High-performance futures trading with extensive altcoin support.
                        </p>

                        <div className="flex items-center gap-3 text-xs font-medium text-text-secondary">
                            <span className="flex items-center gap-1.5 bg-background-dark py-1 px-2 rounded-md">
                                <Activity size={14} /> Low Latency
                            </span>
                            <span className="flex items-center gap-1.5 bg-background-dark py-1 px-2 rounded-md">
                                <Globe size={14} /> Global
                            </span>
                        </div>
                    </button>

                    {/* Binance Card */}
                    <button
                        onClick={() => selectExchange('binance')}
                        className="group relative bg-card-dark border border-border-color hover:border-accent-yellow p-8 rounded-3xl transition-all duration-300 hover:shadow-2xl hover:shadow-accent-yellow/10 text-left hover:-translate-y-1"
                    >
                        <div className="absolute top-6 right-6 p-2 bg-background-dark rounded-full border border-border-color group-hover:border-accent-yellow/50 transition-colors">
                            <ArrowRight className="w-5 h-5 text-text-secondary group-hover:text-accent-yellow" />
                        </div>

                        <div className="w-16 h-16 bg-[#FCD535]/10 rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-500">
                            <span className="text-3xl font-bold text-[#FCD535]">B</span>
                        </div>

                        <h3 className="text-2xl font-bold mb-2">Binance Futures</h3>
                        <p className="text-text-secondary text-sm mb-6">
                            World's largest crypto exchange with deep liquidity and variety.
                        </p>

                        <div className="flex items-center gap-3 text-xs font-medium text-text-secondary">
                            <span className="flex items-center gap-1.5 bg-background-dark py-1 px-2 rounded-md">
                                <Activity size={14} /> Deep Liquidity
                            </span>
                            <span className="flex items-center gap-1.5 bg-background-dark py-1 px-2 rounded-md">
                                <Globe size={14} /> Tier 1
                            </span>
                        </div>
                    </button>

                </div>

                <div className="mt-16 text-text-secondary text-xs opacity-50">
                    v3.0.0-PRO • SYSTEM OPERATIONAL
                </div>

            </div>
        </div>
    )
}
