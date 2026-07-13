# Design

## Boundary Model

Keep subtitle cues as the durable timing source, but distinguish a sentence-sized speech span from a broad continuous-speech chain. A cut landing inside a cue extends through the current sentence span. Nearby cues only join that span when their timing and text indicate that the sentence continues; terminal punctuation provides a hard sentence boundary.

The existing broad speech-chain behavior remains available to the budget trimmer for finding safe candidate cuts, but final protection must not merge many completed sentences into an unbounded extension.

## Budget Interaction

Replace the old behavior that blindly clips the natural endpoint to `end + 3s`. Final speech protection may extend past 3 seconds to finish the current sentence, subject to a larger pathological-safety cap. If the safety cap is reached, choose the latest sentence boundary within the cap rather than cutting at an arbitrary timestamp.

The configured cap remains a duration guard, not the desired cut location. KDA/combat spans and edge anchors remain unchanged.

## Compatibility

- Existing SRT assets remain valid; no schema or migration is required.
- Match-start short context remains exempt.
- Exporter and edit-plan contracts do not change.
- Configuration retains an environment-controlled safety bound with updated semantics/default if required by implementation evidence.

## Validation

- Multi-cue unfinished sentence needing more than 3 seconds is preserved.
- Terminal punctuation splits adjacent speech into independently trimmable sentences.
- Long continuous commentary remains bounded and can cut at an earlier completed sentence.
- Existing condensed planner regression suite remains green.
