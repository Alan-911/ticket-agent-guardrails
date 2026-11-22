# Break log

Every rule that broke during testing, recorded when it happened.

| # | Date | Rule | Attack / test | What happened | Root cause | Fix | Covered by test |
|---|---|---|---|---|---|---|---|
| 1 | 2026-11-20 | R7 | A-001 direct injection (English) | Flagged as `OVERRIDE` **and** `OVERRIDE_FR`. Blocking was unaffected, but the attribution report would have credited a French-language detector for an English attack. | The French pattern `ignore[zr]?` made the suffix optional, so it matched plain English "ignore". | Require `ignorez`/`ignorer`; same tightening for Spanish. | `test_english_override_is_not_mislabeled_as_french` |
| 2 | 2026-11-20 | R2 | A-005 cross-customer ("exactly like Harborline Outfitters' last order") | The tool layer denied every attempt to read Harborline's data, but intake raised **no flag**, so the estimator wasn't warned that the sender was probing for another customer's prices. | Intake only checked the sender, not what the email asked about. | Intake now flags `MENTIONS_OTHER_CUSTOMER` when the email names another customer's company or domain. | `test_R2_email_naming_another_customer_is_flagged` |
