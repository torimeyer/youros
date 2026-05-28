
interface MyOSLogoProps {
  size?: number
  className?: string
}

export default function MyOSLogo({ size = 48, className = '' }: MyOSLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <linearGradient id="myos-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="20%" stopColor="#06b6d4" />
          <stop offset="40%" stopColor="#10b981" />
          <stop offset="60%" stopColor="#f59e0b" />
          <stop offset="80%" stopColor="#ef4444" />
          <stop offset="100%" stopColor="#ec4899" />
        </linearGradient>
        <linearGradient id="myos-accent-gradient" x1="100%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#8b5cf6" />
          <stop offset="50%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#06b6d4" />
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
      
      {/* Background circles for depth */}
      <circle cx="50" cy="50" r="45" fill="url(#myos-gradient)" fillOpacity="0.15" />
      <circle cx="50" cy="50" r="40" stroke="url(#myos-accent-gradient)" strokeWidth="1" fillOpacity="0.05" />

      {/* Main Logo Shape: A stylized 'm' that looks like a path or wave */}
      <path
        d="M20 70V40C20 31.7157 26.7157 25 35 25C43.2843 25 50 31.7157 50 40V70M50 40C50 31.7157 56.7157 25 65 25C73.2843 25 80 31.7157 80 40V70"
        stroke="url(#myos-gradient)"
        strokeWidth="10"
        strokeLinecap="round"
        strokeLinejoin="round"
        filter="url(#glow)"
      />
      
      {/* Dynamic accents */}
      <circle cx="35" cy="40" r="3" fill="white" fillOpacity="0.5" />
      <circle cx="65" cy="40" r="3" fill="white" fillOpacity="0.5" />
    </svg>
  )
}
