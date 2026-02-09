export const Logo = ({ size = 48 }: { size?: number }) => (
  <svg 
    width={size} 
    height={size} 
    viewBox="0 0 512 512" 
    fill="none" 
    xmlns="http://www.w3.org/2000/svg"
  >
    <defs>
      <linearGradient id="brandGradient" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#007acc" />
        <stop offset="100%" stopColor="#4EC9B0" />
      </linearGradient>
    </defs>
    <rect width="512" height="512" rx="100" fill="#1e1e1e"/>
    <g transform="translate(140, 130)">
      <path 
        d="M 0 0 H 160 C 200 0 232 32 232 72 V 100 C 232 140 200 172 160 172 H 60 V 250 H 0 V 0 Z" 
        fill="url(#brandGradient)" 
      />
      <rect x="60" y="60" width="100" height="52" rx="4" fill="#1e1e1e" />
      <path d="M 180 -20 L 250 -20 L 250 50 Z" fill="#FFFFFF" />
    </g>
  </svg>
);
