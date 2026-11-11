---
name: cardy-add-cards
description: Add proposed writing cards to Cardy after the user explicitly approves the exact batch and target deck in the current chat.
---

# Add cards to Cardy

Use the `cardy` MCP connection to check phrases and `cardy-add` to add cards to
**production Cardy**. Only when the user explicitly chooses the local test deck,
use `cardy-local` and `cardy-local-add` instead. Show the target deck in the batch
preview and obtain approval for that target; never silently switch decks or
bypass a missing connection with SQL, shell commands, browser automation, or
another credential.

Accept 1–10 cards with `obverse`, `reverse`, and `extra` strings. Obverse contains
the correct phrase. Reverse contains only `Previously: [exact original]` and
`Better: [natural corrected example]` on separate lines. Extra is normally blank.
Limits are 300 / 2,000 / 4,000 characters respectively. New cards start with the
reverse hidden. Do not edit existing cards.

Before adding, show the exact card contents and target deck in the current chat,
then wait for explicit approval of that batch. Approval to install this skill,
set up MCP, schedule reviews, or generate suggestions is not approval to add
cards. If the exact contents are already visible and the user has approved them,
proceed without asking again. Changes to the approved contents require renewed
approval. A phrase-only list does not show the exact reverse text for approval.

Never invoke a write tool during a scheduled or unattended run, even with standing
instructions to add cards daily. In that situation, return suggestions and await
a new user message. Do not add cards merely because this skill was selected.

After approval, check the Obverses through the read-only connection and omit
existing matches. If the check fails, stop and report that duplication was not
verified. Do not replace omitted cards without approval.

Call `add_cards` on the approved deck's add connection with only the approved remaining cards,
`approved: true`, and a fresh UUID `batch_id`. Preserve the exact request and UUID
when retrying after a timeout; do not create a new ID to work around an uncertain
result. Respect the tool's approval prompt. Never claim success from submission
alone: use the returned added/skipped counts. Report the target deck and counts
briefly. If no new cards remain, report that nothing was added.
