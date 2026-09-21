import { translationSections } from '../lib/translation'

export function NoBsTranslation({ text, empty = 'No translation was extracted.', tone = 'text-white/85' }) {
  const sections = translationSections(text)
  if (!sections) {
    return <p className={`mt-3 text-sm leading-6 ${tone}`}>{text || empty}</p>
  }
  return (
    <dl className="mt-3 space-y-3">
      {sections.map(({ label, body }) => (
        <div key={label}>
          <dt className="font-mono text-[11px] uppercase tracking-[0.071em] text-white/60">{label}</dt>
          <dd className={`mt-1 text-sm leading-6 ${tone}`}>{body}</dd>
        </div>
      ))}
    </dl>
  )
}
