interface YourOSLogoProps {
  size?: number
  className?: string
  showTagline?: boolean
}

export default function YourOSLogo({ size = 48, className = '', showTagline = false }: YourOSLogoProps) {
  const viewW = 340
  const viewH = 160
  const w = Math.round(size * (viewW / viewH))

  return (
    <svg
      width={w}
      height={size}
      viewBox={`0 0 ${viewW} ${viewH}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <linearGradient id="yos-vG7" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#f7a26b" />
          <stop offset="48%" stopColor="#e85f7c" />
          <stop offset="100%" stopColor="#7c2d6a" />
        </linearGradient>
        <linearGradient id="yos-sky7" x1="50%" y1="0%" x2="50%" y2="100%">
          <stop offset="0%" stopColor="#5b21b6" />
          <stop offset="38%" stopColor="#be185d" />
          <stop offset="70%" stopColor="#f97316" />
          <stop offset="100%" stopColor="#fed7aa" />
        </linearGradient>
        <radialGradient id="yos-sun7" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stopColor="#fff3c4" />
          <stop offset="55%" stopColor="#fbbf24" />
          <stop offset="100%" stopColor="#ef6b4f" />
        </radialGradient>
        <clipPath id="yos-clip7">
          <circle cx="212" cy="60" r="25" />
        </clipPath>
      </defs>

      <g transform="rotate(-2 165 70)">
        {/* "your" wordmark */}
        <text
          x="188"
          y="92"
          textAnchor="end"
          fontFamily="'Marker Felt','Caveat','Bradley Hand',cursive"
          fontSize={84}
          fontWeight="700"
          fill="url(#yos-vG7)"
          style={{ letterSpacing: '-1px' }}
        >
          your
        </text>

        {/* O = sunset disc scene */}
        <circle cx="212" cy="60" r="25" fill="url(#yos-sky7)" stroke="#3b0764" strokeWidth="3" />
        <g clipPath="url(#yos-clip7)">
          <line x1="187" y1="68" x2="237" y2="68" stroke="#3b0764" strokeWidth="1.4" opacity="0.85" />
          <line x1="187" y1="69.1" x2="237" y2="69.1" stroke="#fed7aa" strokeWidth="0.6" opacity="0.9" />
          <circle cx="212" cy="64" r="10" fill="url(#yos-sun7)" />
          <ellipse cx="212" cy="68.5" rx="17" ry="2" fill="#fcd34d" opacity="0.55" />
        </g>

        {/* "S" */}
        <text
          x="242"
          y="92"
          textAnchor="start"
          fontFamily="'Marker Felt','Caveat','Bradley Hand',cursive"
          fontSize={84}
          fontWeight="700"
          fill="url(#yos-vG7)"
          style={{ letterSpacing: '-1px' }}
        >
          S
        </text>

        {/* hand-drawn underline flourish */}
        <path
          d="M50 112 Q 150 126, 280 106"
          stroke="url(#yos-vG7)"
          strokeWidth="3.2"
          strokeLinecap="round"
          fill="none"
          opacity="0.9"
        />
      </g>

      {showTagline && (
        <text
          x="165"
          y="148"
          textAnchor="middle"
          fontFamily="'Inter','Helvetica Neue',system-ui,sans-serif"
          fontSize={13}
          fontStyle="italic"
          fill="#cbd5e1"
          style={{ letterSpacing: '.04em' }}
        >
          {'an OS that '}
          <tspan fontStyle="normal" fill="#fbbf24">knows you</tspan>
          {'.'}
        </text>
      )}
    </svg>
  )
}
