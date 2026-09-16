export default function MatchaMark({ className = 'h-4 w-4' }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* three curls of steam, the middle one tallest */}
      <path d="M7.5 5.2c-.8.7-.8 1.6 0 2.3" />
      <path d="M12 3.4c-.9.8-.9 1.8 0 2.6s.9 1.8 0 2.6" />
      <path d="M16.5 5.2c-.8.7-.8 1.6 0 2.3" />
      {/* the cup: square shoulders, round base — the shape that still reads at 16px */}
      <path d="M3.5 11h13v4.5a5 5 0 0 1-5 5h-3a5 5 0 0 1-5-5V11Z" />
      {/* handle */}
      <path d="M16.5 12.5H18a2.75 2.75 0 0 1 0 5.5h-1.5" />
    </svg>
  )
}
