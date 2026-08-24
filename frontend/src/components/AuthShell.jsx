import { Link } from 'react-router-dom'

export default function AuthShell({ title, description, children, footer }) {
  return (
    <main className="grid min-h-screen place-items-center bg-surface px-4 py-10">
      <section className="surface-card w-full max-w-md p-6 animate-page-in sm:p-8">
        <Link to="/dashboard" className="text-base font-medium text-ink">JD Translator</Link>
        <h1 className="mt-8 text-base font-medium text-ink">{title}</h1>
        {description && <p className="mt-2 text-sm leading-5 text-muted">{description}</p>}
        <div className="mt-6">{children}</div>
        <p className="mt-6 text-center text-sm text-muted">{footer}</p>
      </section>
    </main>
  )
}
