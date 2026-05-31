# Age Grouping Notes

## Safe Edition And Noise Normalization

Age restriction grouping intentionally uses a narrower identity than duplicate hiding. The age group key may ignore safe same-movie edition, restoration, remaster, presentation, and release-noise wording when the parser has a usable title and year. This lets different editions of the same movie share one family age requirement.

This is not an alias system. Acronyms, regional titles, franchise shorthand, and alternate titles still remain separate unless a future explicit alias mechanism is designed.

Regression guards:

- Keep age grouping conservative: same title and same year only.
- Keep missing-year or suspicious parser output on the per-item fallback key.
- Do not use fuzzy matching, assistant/LLM grouping, or manual alias links here.
- Do not change duplicate hiding to follow the age grouping key; local duplicate hiding still keeps edition and filename signals.
