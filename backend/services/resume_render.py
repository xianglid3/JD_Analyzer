"""Turn accepted edits into a resume document — HTML to print, or LaTeX to compile."""

import re

from jinja2 import Environment, StrictUndefined, select_autoescape

from services.resume_evidence import load_resume_header

KIND_TITLES = [
    ("experience", "Experience"),
    ("project", "Projects"),
    ("education", "Education"),
    ("certificate", "Certificates"),
]

LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
LATEX_PATTERN = re.compile("|".join(re.escape(c) for c in LATEX_ESCAPES))


def latex_escape(value):
    """Without this, an "R&D" bullet breaks the compile and a backslash runs as a command."""
    if value is None:
        return ""
    return LATEX_PATTERN.sub(lambda m: LATEX_ESCAPES[m.group()], str(value))


def build_document(cur, user_id, run_id=None):
    """The resume with this run's accepted edits swapped in.

    The swap happens here rather than in resume_bullets, because those bullets are shared by
    every job and are what all the citations point at.
    """
    header = load_resume_header(cur, user_id)

    replacements = {}
    if run_id is not None:
        cur.execute(
            """
            SELECT bullet_id, proposed_text
            FROM proposed_edits
            WHERE run_id = %s AND user_id = %s AND status = 'accepted' AND bullet_id IS NOT NULL
            ORDER BY created_at
            """,
            (run_id, user_id),
        )
        replacements = {str(row[0]): row[1] for row in cur.fetchall()}

    cur.execute(
        """
        SELECT e.id, e.kind, e.organization, e.title, e.location, e.start_date, e.end_date,
               b.id, b.text, b.sort_order
        FROM resume_entries AS e
        LEFT JOIN resume_bullets AS b ON b.entry_id = e.id
        WHERE e.user_id = %s
        ORDER BY e.sort_order, e.id, b.sort_order
        """,
        (user_id,),
    )

    entries = {}
    order = []
    for row in cur.fetchall():
        entry_id = str(row[0])
        if entry_id not in entries:
            entries[entry_id] = {
                "kind": row[1], "organization": row[2], "title": row[3],
                "location": row[4], "start_date": row[5], "end_date": row[6],
                "bullets": [],
            }
            order.append(entry_id)
        if row[7] is not None:
            bullet_id = str(row[7])
            entries[entry_id]["bullets"].append({
                "text": replacements.get(bullet_id, row[8]),
                "tailored": bullet_id in replacements,
            })

    all_entries = [entries[entry_id] for entry_id in order]
    sections = [
        {"kind": kind, "title": title, "entries": [e for e in all_entries if e["kind"] == kind]}
        for kind, title in KIND_TITLES
    ]

    return {
        "header": header,
        "sections": [s for s in sections if s["entries"]],
        "skills": [
            bullet["text"]
            for entry in all_entries if entry["kind"] == "skill"
            for bullet in entry["bullets"]
        ],
        "tailored_count": sum(
            1 for entry in all_entries for bullet in entry["bullets"] if bullet["tailored"]
        ),
    }


def _contact_line(header):
    parts = [header.get("location"), header.get("phone"), header.get("email")]
    parts += list(header.get("links") or [])
    return [part for part in parts if part]


def _dates(entry):
    start, end = entry.get("start_date"), entry.get("end_date")
    if start and end:
        return f"{start} -- {end}"
    return start or end or ""


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ header.full_name or 'Resume' }}</title>
<style>
  :root { color-scheme: light; }
  body { font-family: Georgia, 'Times New Roman', serif; color: #171717; max-width: 7.5in;
         margin: 0 auto; padding: 0.5in; line-height: 1.4; font-size: 11pt; }
  h1 { font-size: 20pt; margin: 0 0 4px; letter-spacing: 0.02em; }
  .contact { font-size: 9.5pt; color: #4d4d4d; margin-bottom: 18px; }
  h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 0.08em;
       border-bottom: 1px solid #171717; padding-bottom: 2px; margin: 18px 0 8px; }
  .entry { margin-bottom: 10px; }
  .entry-head { display: flex; justify-content: space-between; gap: 12px; }
  .entry-title { font-weight: bold; }
  .entry-meta { font-size: 9.5pt; color: #4d4d4d; white-space: nowrap; }
  ul { margin: 4px 0 0; padding-left: 18px; }
  li { margin-bottom: 2px; }
  .skills { font-size: 10.5pt; }
  @media print { body { padding: 0.4in; } }
</style>
</head>
<body>
  <h1>{{ header.full_name or 'Your name' }}</h1>
  {% if contact %}<p class="contact">{{ contact | join(' · ') }}</p>{% endif %}

  {% for section in sections %}
  <section>
    <h2>{{ section.title }}</h2>
    {% for entry in section.entries %}
    <div class="entry">
      <div class="entry-head">
        <span class="entry-title">{{ entry.title or entry.organization or '' }}{% if entry.title and entry.organization %} — {{ entry.organization }}{% endif %}</span>
        <span class="entry-meta">{{ [entry.location, dates(entry)] | select | join(' · ') }}</span>
      </div>
      {% if entry.bullets %}
      <ul>{% for bullet in entry.bullets %}<li>{{ bullet.text }}</li>{% endfor %}</ul>
      {% endif %}
    </div>
    {% endfor %}
  </section>
  {% endfor %}

  {% if skills %}
  <section>
    <h2>Skills</h2>
    <p class="skills">{{ skills | join(', ') }}</p>
  </section>
  {% endif %}
</body>
</html>
"""

# Jake Gutierrez's resume template (MIT), driven by our data.
# Jinja's delimiters are swapped to << >> and <% %> so they don't collide with LaTeX braces.
LATEX_TEMPLATE = r"""%-------------------------
% Resume in Latex
% Author : Jake Gutierrez
% Based off of: https://github.com/sb2nov/resume
% License : MIT
%------------------------

\documentclass[letterpaper,11pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\input{glyphtounicode}

\pagestyle{fancy}
\fancyhf{}
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}

\urlstyle{same}

\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

\pdfgentounicode=1

\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}

\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}

\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}

\begin{document}

\begin{center}
    \textbf{\Huge \scshape << header.full_name or 'Your name' >>} \\ \vspace{1pt}
    \small << contact | join(sep) >>
\end{center}

<% for section in sections %>
\section{<< section.title >>}
  \resumeSubHeadingListStart
<% for entry in section.entries %>
<% if section.kind == 'project' %>
    \resumeProjectHeading
      {\textbf{<< entry.title or entry.organization >>}<% if entry.organization and entry.title %> $|$ \emph{<< entry.organization >>}<% endif %>}{<< dates(entry) >>}
<% else %>
    \resumeSubheading
      {<< entry.title or entry.organization >>}{<< dates(entry) >>}
      {<< entry.organization if entry.title else '' >>}{<< entry.location or '' >>}
<% endif %>
<% if entry.bullets %>
      \resumeItemListStart
<% for bullet in entry.bullets %>        \resumeItem{<< bullet.text >>}
<% endfor %>      \resumeItemListEnd
<% endif %>
<% endfor %>
  \resumeSubHeadingListEnd
<% endfor %>
<% if skills %>
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Languages, tools and frameworks}{: << skills | join(', ') >>}
    }}
 \end{itemize}
<% endif %>

\end{document}
"""


def render_html(document):
    env = Environment(autoescape=select_autoescape(default=True), undefined=StrictUndefined)
    return env.from_string(HTML_TEMPLATE).render(
        header=document["header"],
        contact=_contact_line(document["header"]),
        sections=document["sections"],
        skills=document["skills"],
        dates=_dates,
    )


def render_latex(document):
    env = Environment(
        block_start_string="<%", block_end_string="%>",
        variable_start_string="<<", variable_end_string=">>",
        comment_start_string="<#", comment_end_string="#>",
        trim_blocks=True, lstrip_blocks=True,
        autoescape=False,
        undefined=StrictUndefined,
    )

    escaped = {
        "header": {"full_name": latex_escape(document["header"].get("full_name"))},
        "contact": [latex_escape(part) for part in _contact_line(document["header"])],
        "sections": [
            {
                "kind": section["kind"],
                "title": latex_escape(section["title"]),
                "entries": [
                    {
                        "title": latex_escape(entry["title"]),
                        "organization": latex_escape(entry["organization"]),
                        "location": latex_escape(entry["location"]),
                        "start_date": latex_escape(entry["start_date"]),
                        "end_date": latex_escape(entry["end_date"]),
                        "bullets": [
                            {"text": latex_escape(bullet["text"])} for bullet in entry["bullets"]
                        ],
                    }
                    for entry in section["entries"]
                ],
            }
            for section in document["sections"]
        ],
        "skills": [latex_escape(skill) for skill in document["skills"]],
    }

    return env.from_string(LATEX_TEMPLATE).render(dates=_dates, sep=r" $|$ ", **escaped)
