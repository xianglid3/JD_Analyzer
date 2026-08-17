
def compute_match_score(job_skills, resume_skills):
    if not job_skills:
        return None
    resume_set = {s.strip().lower() for s in resume_skills}
    matched = sum(1 for s in job_skills if s.strip().lower() in resume_set)
    return round(matched / len(job_skills) * 100, 2)
