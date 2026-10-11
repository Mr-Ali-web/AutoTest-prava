# Progress

**What works**

- The bot's default question path targets `data/questions_yhq_v1.json`.
- `data/build_questions_v1.py` generated a 141-question JSON bank and a 141-row review CSV.
- Importing the bot loaded and validated all 141 questions from the configured path.

**Not started / backlog**

- None for connecting the generated question bank. Generate and validate `data/questions_yhq_v1.json` from the question builder.

**Known issues**

- Generated questions have `status: "draft"`; the bot currently serves all JSON rows without review-status filtering. The bot cannot load its question bank until the generator has created the JSON output.

_Keep bullets factual and small; link issues or PRs when useful._
