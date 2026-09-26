import { useState } from 'react'
import { Link } from 'react-router-dom'
import MatchaMark from '../components/MatchaMark'
import './LandingPage.css'

const repository = 'https://github.com/xianglid3/JD_Analyzer'
const steps = [
  { title: 'Understand the role', description: 'Turn a long job posting into clear requirements and a plain-language summary.' },
  { title: 'Find your evidence', description: 'See which requirements your resume supports and where experience is missing.' },
  { title: 'Review every edit', description: 'Compare suggested wording with your original experience. You choose what stays.' },
]

function Example({ step }) {
  if (step === 0) return <>
    <p className="eyebrow">The posting</p>
    <h3>Backend Engineer</h3>
    <p className="landing-quote">“Help build and maintain our backend services. You’ll work with Python, PostgreSQL, and Kubernetes to support our growing platform.”</p>
    <div className="landing-example-result">
      <p className="eyebrow">In plain language</p>
      <p>Build backend services in Python, work with a PostgreSQL database, and help run services on Kubernetes.</p>
    </div>
    <div className="landing-tags"><span>Python</span><span>PostgreSQL</span><span>Kubernetes</span></div>
  </>
  if (step === 1) return <>
    <p className="eyebrow">Requirement → resume evidence</p>
    <h3>A match you can inspect.</h3>
    <div className="landing-evidence-row"><span>Python</span><span>Explicit evidence</span></div>
    <p className="landing-quote">“Built REST API endpoints in Python with a PostgreSQL database.”</p>
    <div className="landing-evidence-row"><span>PostgreSQL</span><span>Explicit evidence</span></div>
    <div className="landing-evidence-row"><span>Kubernetes</span><span>No evidence found</span></div>
    <p className="landing-demo-note">A missing skill stays visible as a gap.</p>
  </>
  return <>
    <p className="eyebrow">Suggested resume edit</p>
    <h3>Your experience, more clearly.</h3>
    <div className="landing-edit"><p className="eyebrow">Original</p><p>Worked on Kubernetes deployments across three regions.</p></div>
    <div className="landing-example-result"><p className="eyebrow">Suggested</p><p>Deployed Kubernetes services across three regions.</p></div>
    <p className="landing-demo-note">Linked to the original resume bullet · Review before accepting</p>
  </>
}

export default function LandingPage() {
  const [step, setStep] = useState(0)
  const [videoPlaying, setVideoPlaying] = useState(false)
  return (
    <div className="landing-page">
      <a href="#main" className="landing-skip">Skip to content</a>
      <header className="landing-nav landing-width">
        <Link to="/" className="landing-brand" aria-label="JobMatcha home"><MatchaMark className="h-6 w-6" />JobMatcha</Link>
        <nav aria-label="Main navigation">
          <a href="#how-it-works" className="landing-nav-detail">How it works</a>
          <a href={repository} className="landing-nav-detail">GitHub <span aria-hidden="true">↗</span></a>
          <Link to="/login">Log in</Link>
          <Link to="/signup" className="primary-button">Get started <span aria-hidden="true">↗</span></Link>
        </nav>
      </header>

      <main id="main">
        <section className="landing-hero landing-width">
          <p className="eyebrow">A little clarity for your next move</p>
          <h1>Find the fit.<br /><span>Make your experience count.</span></h1>
          <p className="landing-intro">Understand the job, see how your resume matches, and tailor it with edits grounded in your own experience.</p>
          <div className="landing-actions"><Link to="/signup" className="primary-button">Try JobMatcha <span aria-hidden="true">↗</span></Link><a href="#how-it-works" className="secondary-button">Explore the walkthrough <span aria-hidden="true">↓</span></a></div>
          <p className="landing-hero-note">From job posting to a resume you’ve reviewed.</p>
        </section>

        <section id="how-it-works" className="landing-walkthrough landing-width" aria-labelledby="walkthrough-heading">
          <div className="landing-section-label"><h2 id="walkthrough-heading" className="eyebrow">01 / From posting to possibility</h2><span className="eyebrow">Illustrative examples</span></div>
          <div className="landing-demo">
            <div className="landing-steps" role="group" aria-label="Walkthrough steps">
              {steps.map((item, index) => <button key={item.title} type="button" className={`landing-step ${step === index ? 'is-active' : ''}`} aria-pressed={step === index} aria-controls="walkthrough-preview" onClick={() => setStep(index)}>
                <span className="landing-step-number">0{index + 1}</span><span><span className="landing-step-title">{item.title}</span><span className="landing-step-description">{item.description}</span></span><span aria-hidden="true">↗</span>
              </button>)}
            </div>
            <div className="landing-preview" id="walkthrough-preview" role="region" aria-label={steps[step].title} aria-live="polite" aria-atomic="true">
              <div className="landing-preview-top"><span><MatchaMark className="h-4 w-4" /> JobMatcha</span><span>Sample walkthrough / 0{step + 1}</span></div>
              <div className="landing-preview-body"><Example step={step} /></div>
            </div>
          </div>
        </section>

        <section className="landing-video landing-width" aria-labelledby="demo-video-heading">
          <div className="landing-section-label"><h2 id="demo-video-heading" className="eyebrow">02 / See it in action</h2><span className="eyebrow">Real tailoring run</span></div>
          <div className="landing-video-heading">
            <h2>Watch JobMatcha work<br /><span>from job post to suggested edits.</span></h2>
            <p>See how JobMatcha reviews a resume, asks for missing details, and turns those answers into edits you can approve.</p>
          </div>
          <div className="landing-video-frame">
            {videoPlaying ? (
              <iframe
                src="https://www.youtube-nocookie.com/embed/kP2k26Xvk8E?autoplay=1&rel=0"
                title="JobMatcha example resume tailoring run"
                referrerPolicy="strict-origin-when-cross-origin"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
              />
            ) : (
              <button className="landing-video-poster" type="button" onClick={() => setVideoPlaying(true)} aria-label="Play the JobMatcha tailoring demo">
                <img src="https://img.youtube.com/vi/kP2k26Xvk8E/maxresdefault.jpg" alt="JobMatcha tailoring run showing questions generated from a resume" loading="lazy" />
                <span className="landing-video-play" aria-hidden="true">▶</span>
              </button>
            )}
          </div>
        </section>

        <section className="landing-engineering landing-width" aria-labelledby="engineering-heading">
          <p className="eyebrow">03 / Under the hood</p>
          <div className="landing-section-heading"><h2 id="engineering-heading">Thoughtful on the surface.<br /><span>Deliberate underneath.</span></h2><p>A full-stack project exploring a practical question: how do you make model-assisted resume editing inspectable and reliable?</p></div>
          <div className="landing-features">
            <article><span className="eyebrow">01 — Traceability</span><h3>Evidence behind the edit.</h3><p>Proposed edits link to resume evidence. Backend checks validate citations and reject specific unsupported claims, including added technologies and numbers.</p></article>
            <article><span className="eyebrow">02 — Explainability</span><h3>A fit you can follow.</h3><p>A deterministic matching engine separates explicit evidence, inferred skills, related experience, and gaps so you can inspect the reasoning behind a match.</p></article>
            <article><span className="eyebrow">03 — Reliability</span><h3>Built for interrupted work.</h3><p>Background workers use leases and checkpoints to resume interrupted runs. Database-backed budget reservations control model spending across concurrent requests.</p></article>
          </div>
          <div className="landing-stack"><p>React <span>/</span> Python & Flask <span>/</span> PostgreSQL <span>/</span> OpenAI</p><a href={repository}>Explore the source <span aria-hidden="true">↗</span></a></div>
        </section>

        <section className="landing-cta landing-width"><div><p className="eyebrow">Your next application starts here</p><h2>Give your experience<br />a clearer voice.</h2></div><Link to="/signup" className="primary-button">Get started with JobMatcha <span aria-hidden="true">↗</span></Link></section>
      </main>
      <footer className="landing-footer landing-width"><Link to="/" className="landing-brand"><MatchaMark className="h-5 w-5" />JobMatcha</Link><p>Understand the role. Tell your story.</p><a href={repository}>View on GitHub <span aria-hidden="true">↗</span></a></footer>
    </div>
  )
}
