import { GlobeIcon, InlineField, TrashIcon } from './InlineField'

export { TrashIcon }

/** The posting link, as an inline field. Kept as its own name because two pages ask for
 *  "the source link" rather than "an inline field that happens to hold a URL". */
export function SourceLink({ job, onSave, saving, grow = false }) {
  return (
    <InlineField
      value={job.source_url || ''}
      display={job.source_url?.replace(/^https?:\/\/(www\.)?/, '')}
      href={job.source_url}
      onSave={onSave}
      saving={saving}
      icon={GlobeIcon}
      label={`link for ${job.title || 'job'}`}
      placeholder="https://…"
      emptyText="No link"
      copyable
      grow={grow}
      width={grow ? 'w-64' : 'w-full'}
    />
  )
}
