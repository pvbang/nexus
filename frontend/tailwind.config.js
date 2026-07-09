/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        display: ['Outfit', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        mid: ['Poppins', 'sans-serif'],
        data: ['Roboto', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      colors: {
        // ── Brand (static numeric scale for gradients) ──
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1456f0',
          900: '#17437d',
          pink: '#ea5ec1',
          sky: '#3daeff',
          // Semantic (auto-switch via CSS vars)
          primary: 'var(--color-accent)',
          secondary: 'var(--color-accent-secondary)',
        },
        // ── Surface (auto-switch via CSS vars) ──
        surface: {
          base: 'var(--color-bg-primary)',
          raised: 'var(--color-bg-raised)',
          secondary: 'var(--color-bg-secondary)',
          tertiary: 'var(--color-bg-tertiary)',
          inset: 'var(--color-bg-inset)',
          overlay: 'var(--color-bg-overlay)',
          // Static (kept for backward compat)
          DEFAULT: '#ffffff',
          dark: '#181e25',
          glass: 'hsla(0, 0%, 100%, 0.4)',
        },
        // ── Text (auto-switch via CSS vars) ──
        text: {
          heading: 'var(--color-text-heading)',
          primary: 'var(--color-text-primary)',
          secondary: 'var(--color-text-secondary)',
          muted: 'var(--color-text-muted)',
          // Static (kept for backward compat)
          helper: '#5f5f5f',
          dark: '#18181b',
        },
        // ── Border (auto-switch via CSS vars) ──
        border: {
          subtle: 'var(--color-border-light)',
          DEFAULT: 'var(--color-border-default)',
          hover: 'var(--color-border-hover)',
          // Static (kept for backward compat)
          light: '#f2f3f5',
          dark: '#d1d5db',
        },
        // ── Status ──
        status: {
          success: '#22c55e',
          'success-bg': '#e8ffea',
          warning: '#f59e0b',
          error: '#ef4444',
          info: '#3b82f6',
        },
      },
      borderRadius: {
        'sm': '4px',
        'DEFAULT': '8px',
        'md': '8px',
        'comfortable': '11px',
        'lg': '13px',
        'generous': '16px',
        'xl': '20px',
        'hero': '24px',
        'pill': '30px',
        'full-pill': '9999px',
      },
      boxShadow: {
        'subtle': '0 4px 6px rgba(0, 0, 0, 0.08)',
        'ambient': '0 0 22.576px rgba(0, 0, 0, 0.08)',
        'brand': '0 0 15px rgba(44, 30, 116, 0.16)',
        'brand-offset': '6.5px 2px 17.5px rgba(44, 30, 116, 0.11)',
        'elevated': '0 12px 16px -4px rgba(36, 36, 36, 0.08)',
        'card': '0 1px 3px rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.06)',
      },
      spacing: {
        '4.5': '1.125rem',
        '13': '3.25rem',
        '15': '3.75rem',
        '18': '4.5rem',
      },
      lineHeight: {
        'tight': '1.10',
        'snug': '1.25',
        'normal': '1.50',
        'relaxed': '1.70',
      },
      fontSize: {
        'micro': ['0.625rem', { lineHeight: '1.50' }],   // 10px
        'tiny': ['0.75rem', { lineHeight: '1.50' }],      // 12px
        'caption': ['0.8125rem', { lineHeight: '1.70' }], // 13px
        'sm': ['0.875rem', { lineHeight: '1.50' }],       // 14px
        'base': ['1rem', { lineHeight: '1.50' }],         // 16px
        'feature': ['1.125rem', { lineHeight: '1.50' }],  // 18px
        'body-lg': ['1.25rem', { lineHeight: '1.50' }],   // 20px
        'subhead': ['1.5rem', { lineHeight: '1.50' }],    // 24px
        'card-title': ['1.75rem', { lineHeight: '1.71' }],// 28px
        'section': ['1.9375rem', { lineHeight: '1.50' }], // 31px
        'section-alt': ['2rem', { lineHeight: '0.88' }],  // 32px
        'display': ['5rem', { lineHeight: '1.10' }],      // 80px
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'slide-down': 'slideDown 0.2s ease-out',
        'scale-in': 'scaleIn 0.2s ease-out',
        'pulse-dot': 'pulseDot 2s ease-in-out infinite',
        // ── Enhanced Animations ──
        'shimmer': 'shimmer 2s linear infinite',
        'glow-pulse': 'glowPulse 2s ease-in-out infinite',
        'border-flow': 'borderFlow 3s ease-in-out infinite',
        'float-in': 'floatIn 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
        'slide-in-right': 'slideInRight 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
        'bounce-in': 'bounceIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)',
        'gradient-shift': 'gradientShift 4s ease-in-out infinite',
        'orbital-spin': 'orbitalSpin 1.5s linear infinite',
        'status-flash-success': 'statusFlashSuccess 0.6s ease-out',
        'status-flash-error': 'statusFlashError 0.6s ease-out',
        'icon-fade-in': 'iconFadeIn 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
        'border-pulse': 'borderPulse 2s ease-in-out infinite',
        'thinking-pulse': 'thinkingPulse 1.8s ease-in-out infinite',
        'phase-complete': 'phaseComplete 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideDown: {
          '0%': { opacity: '0', transform: 'translateY(-8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        pulseDot: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
        // ── Enhanced Keyframes ──
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        glowPulse: {
          '0%, 100%': { boxShadow: '0 0 4px rgba(59, 130, 246, 0.3)' },
          '50%': { boxShadow: '0 0 16px rgba(59, 130, 246, 0.6), 0 0 32px rgba(59, 130, 246, 0.2)' },
        },
        borderFlow: {
          '0%, 100%': { borderColor: 'rgba(59, 130, 246, 0.4)' },
          '50%': { borderColor: 'rgba(139, 92, 246, 0.6)' },
        },
        floatIn: {
          '0%': { opacity: '0', transform: 'translateY(16px) scale(0.96)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(-12px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        bounceIn: {
          '0%': { opacity: '0', transform: 'scale(0.3)' },
          '50%': { transform: 'scale(1.05)' },
          '70%': { transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        gradientShift: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        orbitalSpin: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        statusFlashSuccess: {
          '0%': { boxShadow: '0 0 0 0 rgba(34, 197, 94, 0.4)' },
          '50%': { boxShadow: '0 0 0 4px rgba(34, 197, 94, 0.2)' },
          '100%': { boxShadow: '0 0 0 0 rgba(34, 197, 94, 0)' },
        },
        statusFlashError: {
          '0%': { boxShadow: '0 0 0 0 rgba(239, 68, 68, 0.4)' },
          '50%': { boxShadow: '0 0 0 4px rgba(239, 68, 68, 0.2)' },
          '100%': { boxShadow: '0 0 0 0 rgba(239, 68, 68, 0)' },
        },
        iconFadeIn: {
          '0%': { opacity: '0', transform: 'scale(0.5) rotate(-10deg)' },
          '100%': { opacity: '1', transform: 'scale(1) rotate(0deg)' },
        },
        borderPulse: {
          '0%, 100%': { borderLeftColor: 'rgba(59, 130, 246, 0.6)' },
          '50%': { borderLeftColor: 'rgba(139, 92, 246, 0.9)' },
        },
        thinkingPulse: {
          '0%, 100%': { opacity: '0.7' },
          '50%': { opacity: '1' },
        },
        phaseComplete: {
          '0%': { transform: 'scale(0.8)', opacity: '0.5' },
          '60%': { transform: 'scale(1.15)' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
