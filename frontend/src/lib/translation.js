// The labels the backend stores each section under (`TRANSLATION_SECTIONS` in
// openai_services.py), in the order they are shown.
const SECTIONS = ['What the role is', 'What skills they expect', 'Day to day']

/** Split a labelled translation into its sections, or null for an older free-text one. */
export function translationSections(text) {
  if (!text) return null
  const sections = text
    .split(/\n\s*\n/)
    .map((block) => {
      const label = SECTIONS.find((name) => block.startsWith(`${name}:`))
      return label ? { label, body: block.slice(label.length + 1).trim() } : null
    })
  return sections.length && sections.every(Boolean) ? sections : null
}
