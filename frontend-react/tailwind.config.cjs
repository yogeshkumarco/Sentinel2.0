/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                primary: "#4f9eff",           // --accent
                "background-light": "#f8fafc",
                "background-dark": "#0a0e17", // --bg-dark
                "card-dark": "#141b2d",       // --bg-card
                "bg-hover": "#1f2940",        // --bg-hover
                "accent-green": "#00d26a",    // --success
                "accent-red": "#ff4757",      // --danger
                "accent-yellow": "#ffa726",   // --warning
                "text-secondary": "#8b9dc3",  // --text-secondary
                "border-color": "#2a3a5a",    // --border
            },
            fontFamily: {
                sans: ["Inter", "sans-serif"],
                mono: ["JetBrains Mono", "monospace"],
            },
            borderRadius: {
                DEFAULT: "12px",
            },
            boxShadow: {
                'glow-green': '0 0 20px rgba(0, 210, 106, 0.4)',
                'glow-red': '0 0 20px rgba(255, 71, 87, 0.3)',
                'glow-blue': '0 0 20px rgba(79, 158, 255, 0.3)',
            }
        },
    },
    plugins: [],
}
