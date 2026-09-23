import { Link } from 'react-router-dom'
import MatchaMark from './MatchaMark'

export default function AuthShell({ title, description, children, footer }) {
  return (
    <main className="grid min-h-screen place-items-center bg-surface px-4 py-10">
      <section className="surface-card w-full max-w-md p-6 animate-page-in sm:p-8">
        <Link to="/" className="inline-flex items-center gap-2 text-sm font-medium text-ink">
          <MatchaMark className="h-[18px] w-[18px] text-carbon" />
          JobMatcha
        </Link>
        <p className="eyebrow mt-8">Account access</p>
        <h1 className="page-heading mt-2">{title}</h1>
        {description && <p className="mt-2 text-sm leading-5 text-muted">{description}</p>}
        <div className="mt-6">{children}</div>
        <p className="mt-6 text-center text-sm text-muted">{footer}</p>
      </section>
    </main>
  )
}
