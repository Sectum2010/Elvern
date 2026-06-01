# Age Grouping Notes

## Safe Edition And Noise Normalization

Age restriction grouping intentionally uses a narrower identity than duplicate hiding. The age group key may ignore safe same-movie edition, restoration, remaster, presentation, and release-noise wording when the parser has a usable title and year. This lets different editions of the same movie share one family age requirement.

This is not an alias system. Acronyms, regional titles, franchise shorthand, and alternate titles still remain separate unless a future explicit alias mechanism is designed.

Regression guards:

- Keep age grouping conservative: same title and same year only.
- Keep missing-year or suspicious parser output on the per-item fallback key.
- Roman numerals normalize only in explicit numbered contexts such as Episode, Part, Chapter, or Volume. A final token like `V` or `X` is not globally converted.
- `3D` is not stripped globally. It is removed only when parser metadata already indicates an obvious trailing technical/release suffix.
- Manual age group links are explicit admin actions for age access only. They are reversible, audited, and do not change media rows, poster matching, search, or duplicate hiding.
- Do not use fuzzy matching, assistant/LLM grouping, semantic aliases, or automatic alias links here.
- Do not change duplicate hiding to follow the age grouping key; local duplicate hiding still keeps edition and filename signals.

## Admin Manual Links

Admins can link a specific media item to an existing age group when the conservative automatic title/year key is intentionally separate. Resolution order is:

1. explicit manual link for that media item;
2. conservative automatic key from the current parser.

Manual links affect only age requirement lookup and age-based session revocation. They do not merge library entries, do not hide duplicates, and do not alter poster/title identity. Link and unlink actions are logged as `admin.media_age_group.link` and `admin.media_age_group.unlink`.

Future LLM or semantic suggestions may assist admins, but they must remain proposals only. The system must not automatically merge out-of-scope aliases such as LOTR shorthand, Sorcerer/Philosopher regional titles, Raiders/Indiana Jones naming, or Fast/Furious shorthand.
