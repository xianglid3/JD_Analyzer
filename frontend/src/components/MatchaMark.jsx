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
      {/* steam — two short curls, kept inside the top third so they stay legible at 18px */}
      <path d="M10 2.5c-.7.8-.7 1.7 0 2.5s.7 1.7 0 2.5" />
      <path d="M14 3.5c-.5.6-.5 1.2 0 1.8s.5 1.2 0 1.8" />
      {/* the bowl */}
      <path d="M4 10h13v4.5A5.5 5.5 0 0 1 11.5 20h-2A5.5 5.5 0 0 1 4 14.5V10Z" />
      {/* handle */}
      <path d="M17 11.5h1.5a2.5 2.5 0 0 1 0 5H17" />
      {/* saucer */}
      <path d="M3 22h15" />
    </svg>
  )
}
