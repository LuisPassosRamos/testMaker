/** Decorative meridians and parallels inspired by the globe in the school's logo. */
export function GlobePattern({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 400 400" className={className} aria-hidden fill="none" stroke="currentColor" strokeLinecap="round">
      <circle cx="200" cy="200" r="170" strokeWidth="6" />
      <ellipse cx="200" cy="200" rx="80" ry="170" strokeWidth="5" />
      <ellipse cx="200" cy="200" rx="135" ry="170" strokeWidth="4" />
      <path d="M38 140 Q200 100 362 140" strokeWidth="5" />
      <path d="M30 200 Q200 225 370 200" strokeWidth="5" />
      <path d="M45 270 Q200 300 355 270" strokeWidth="5" />
      <path d="M200 10 L200 30 M200 370 L200 392" strokeWidth="8" />
      {Array.from({ length: 14 }, (_, i) => {
        const angle = (-60 + i * 9) * (Math.PI / 180)
        const x1 = 200 + Math.cos(angle) * 182
        const y1 = 200 + Math.sin(angle) * 182
        const x2 = 200 + Math.cos(angle) * 200
        const y2 = 200 + Math.sin(angle) * 200
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth="5" />
      })}
    </svg>
  )
}
