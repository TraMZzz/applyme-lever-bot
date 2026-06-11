# Example inputs — make a fresh clone runnable

The real candidate inputs (`inputs/`, `data/`) are git-ignored (PII boundary), so these synthetic
examples let you run the bot straight from a clone. They also document the exact input shapes.

```bash
mkdir -p data
cp examples/profile.example.json  data/profile.json     # → the CandidateProfile model
cp examples/vacancies.example.txt data/vacancies.txt     # one jobs.lever.co URL per line
cp examples/resume.example.pdf    data/resume.pdf        # the résumé uploaded to /apply

uv run applyme run --vacancies data/vacancies.txt --profile data/profile.json   # dry-run (default)
```

- `profile.example.json` — every field of `CandidateProfile` (see `src/applyme/models.py`); swap in
  your own values. `resume_path` points at `data/resume.pdf`.
- `vacancies.example.txt` — the 5 real target postings (any `jobs.lever.co/<co>/<id>` URL works; non-Lever lines are skipped).
- `resume.example.pdf` — a minimal placeholder résumé; replace with a real one for a meaningful upload.

For the original take-home, the provided `inputs/*.md` are converted to `data/` via
`uv run python scripts/prepare_inputs.py` (that path needs the provided `inputs/`, which are internal/not shipped).
