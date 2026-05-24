import { Link } from 'react-router-dom'

export function Footer() {
  return (
    <footer
      data-testid="footer"
      className="border-t border-slate-800 py-3 px-6 flex items-center justify-center gap-3"
    >
      <Link
        to="/about"
        data-testid="footer-link-about"
        className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
      >
        About
      </Link>
      <span className="text-slate-700 select-none text-xs" aria-hidden="true">·</span>
      <Link
        to="/privacy"
        data-testid="footer-link-privacy"
        className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
      >
        Privacy
      </Link>
    </footer>
  )
}
